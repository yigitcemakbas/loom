"""The live loop: filing accepted, verdict reached, in seconds rather than hours.

This is where the two halves of the design meet. `engine/prior.py` has already
decided what would matter about each focus company, slowly and expensively and
well before now. `ingestion/sec_realtime.py` reports what the market just
learned, at constant cost regardless of universe size. This module joins them,
and the join is deliberately the cheapest step of the three.

The ordering inside one cycle is chosen so the expensive work only ever happens
for filings that survive every cheap filter first:

  1. one feed request covering the entire market,
  2. reject anything already assessed, from a single query,
  3. reject anything not in the focus universe, from an in-memory CIK map,
  4. only now fetch the filing text, one request,
  5. score it against the stored prior, microseconds, no model call.

Steps 1 to 3 usually eliminate everything, and they cost one request and one
query per cycle no matter how busy the market is. That is what lets the
interval be short enough to matter.

The latency this achieves is bounded by the poll interval, not by the work: at
a 60 second interval Loom knows within about a minute of acceptance, against
six hours for the scheduled sweep. Shortening the interval shortens the lag
almost exactly, which is why it is configuration rather than a constant.
"""

import html
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

from app.config import settings
from app.db.session import SessionLocal
from app.engine.earnings_extract import (
    comparable_eps,
    extract_figures,
    is_results_filing,
    surprise_is_credible,
)
from app.engine.reaction import MarketEvent, assess
from app.ingestion.rate_limit import limiter
from app.ingestion.sec_edgar import (
    _ARCHIVES_BASE,
    _CONTENT_EXHIBIT_RE,
    _DOCUMENT_ENTRY_RE,
    _ITEMS_RE,
    SecEdgarAdapter,
)
from app.ingestion.sec_realtime import SecRealtimeFeed
from app.models.company import CompanyTier
from app.repositories.assessment_repository import AssessmentRepository
from app.repositories.company_repository import CompanyRepository
from app.repositories.prior_repository import PriorRepository

logger = logging.getLogger(__name__)

# How far back to load already-seen filings. Comfortably longer than the feed's
# own window, so a restart cannot reassess what it handled before.
SEEN_LOOKBACK_HOURS = 24

# Filing text is truncated before matching. Keyword matching gains nothing from
# the back half of an exhibit-heavy 8-K, and the fast path's promise is that it
# stays fast on the largest filing anyone might send.
MAX_MATCH_CHARS = 60_000

# How often the loop says it is still alive, in seconds. Without this a
# watcher that is working perfectly and seeing nothing produces exactly the
# same output as one that died on its first cycle, which is the failure this
# whole file is least able to afford: it would be discovered only by noticing,
# weeks later, that no event had ever been assessed.
HEARTBEAT_SECONDS = 900

_stop_event = threading.Event()
_thread: threading.Thread | None = None


def _filing_content(notice, adapter: SecEdgarAdapter) -> tuple[str, list[str]]:
    """Return (text_to_match_against, 8-K item numbers).

    Reads the filing itself, which is fiddlier than it sounds and worth
    spelling out, because two shortcuts here both silently produce empty text
    and therefore a confident "nothing matched" for every filing ever
    submitted.

    The feed's link points at EDGAR's *index page*, a navigation shell whose
    stripped text is "This page uses Javascript", "SEC Home", "Company Search"
    and nothing from the filing. And the adapter's exhibit reader returns only
    EX-99 attachments, so an 8-K whose substance sits in its primary document,
    which is most 8-Ks that are not earnings releases, comes back blank.

    So both are fetched: the primary document, and any press release attached
    to it. Item numbers come free from the same index and are a strong signal
    in their own right, 5.02 being an executive departure and 2.02 results.
    """
    accession_nodash = notice.accession.replace("-", "")
    base = f"{_ARCHIVES_BASE}/{int(notice.cik)}/{accession_nodash}"
    header_url = f"{base}/{notice.accession}-index-headers.html"

    try:
        limiter.acquire(header_url)
        response = adapter._client.get(header_url)
        if response.status_code != 200:
            logger.info("Watcher: no index headers (%s) for %s", response.status_code, notice.accession)
            return "", []
        header = html.unescape(response.text)
    except Exception:
        logger.warning("Watcher: index headers failed for %s", notice.accession, exc_info=True)
        return "", []

    items = _ITEMS_RE.findall(header)

    parts: list[str] = []
    budget = MAX_MATCH_CHARS
    for doc_type, sequence, filename in _DOCUMENT_ENTRY_RE.findall(header):
        doc_type, filename = doc_type.strip(), filename.strip()
        if budget <= 0:
            break
        # The filing itself is sequence 1; everything else worth reading is a
        # press release exhibit.
        wanted = sequence.strip() == "1" or _CONTENT_EXHIBIT_RE.match(doc_type)
        if not wanted or not filename.lower().endswith((".htm", ".html", ".txt")):
            continue

        text = adapter._fetch_exhibit_text(f"{base}/{filename}")
        if not text:
            continue
        text = text[:budget]
        budget -= len(text)
        parts.append(text)

    return "\n\n".join(parts), items


def run_cycle(db=None) -> int:
    """One pass. Returns the number of new assessments written."""
    owns_session = db is None
    db = db or SessionLocal()
    try:
        company_repo = CompanyRepository(db)
        # Both tiers that carry a prior are watched. The fast path costs
        # nothing per company and the feed is one request regardless of how
        # many are tracked, so breadth here is close to free.
        watched = company_repo.list_by_tier(CompanyTier.FOCUS)
        watched += company_repo.list_by_tier(CompanyTier.WATCH)
        # CIKs are stored zero-padded to ten, which is the form the feed uses.
        by_cik = {c.cik: c for c in watched if c.cik}
        if not by_cik:
            logger.debug("Watcher: no watched companies with a CIK, nothing to watch.")
            return 0

        feed = SecRealtimeFeed()
        notices = feed.fetch_all_forms()
        if not notices:
            return 0

        assessment_repo = AssessmentRepository(db)
        seen = assessment_repo.seen_external_ids(
            datetime.now(timezone.utc) - timedelta(hours=SEEN_LOOKBACK_HOURS)
        )

        relevant = [
            n for n in notices
            if n.cik in by_cik and n.accession not in seen
        ]
        if not relevant:
            return 0

        logger.info("Watcher: %d new filings for tracked companies.", len(relevant))

        prior_repo = PriorRepository(db)
        adapter = SecEdgarAdapter()

        written = 0
        for notice in relevant:
            company = by_cik[notice.cik]
            text, items = _filing_content(notice, adapter)

            # Item numbers are part of the matchable text on purpose: a
            # prior that lists "chief executive departure" can carry "5.02"
            # as a keyword and fire on the item code alone, before anyone
            # has read a word of the exhibit.
            item_line = " ".join(f"item {i}" for i in items)
            prior = prior_repo.latest_for(company.id)

            # An earnings release is the one event worth spending a model call
            # on. Without the actual figures the engine can match keywords in a
            # results announcement while completely ignoring that EPS came in
            # twenty percent ahead of consensus, which is the single most
            # informative thing in the document.
            eps = revenue = None
            eps_basis = "none"
            if is_results_filing(items):
                figures = extract_figures(text)
                if figures is not None:
                    eps, eps_basis = comparable_eps(figures)
                    revenue = figures.revenue

                    # Checked against the consensus the prior already holds.
                    # An extraction can be a perfectly plausible number and
                    # still be the wrong period, and only the comparison shows
                    # it. Dropping the figure leaves the rest of the
                    # assessment intact.
                    consensus = (prior.expectations or {}).get("eps_estimate") if prior else None
                    if eps is not None and not surprise_is_credible(eps, consensus):
                        eps = None
                        eps_basis = "discarded_implausible"

                    logger.info(
                        "%s filed results: EPS %s (%s), revenue %s",
                        company.ticker, eps, eps_basis, revenue,
                    )

            event = MarketEvent(
                ticker=company.ticker,
                kind="earnings" if is_results_filing(items) else "filing",
                occurred_at=notice.accepted_at or datetime.now(timezone.utc),
                text=f"{notice.form} {notice.company_name} {item_line}\n{text}",
                eps_actual=eps,
                revenue_actual=revenue,
                source_url=notice.index_url,
            )

            result = assess(event, prior)

            latency = None
            if notice.accepted_at is not None:
                latency = (
                    datetime.now(timezone.utc) - notice.accepted_at
                ).total_seconds()

            created = assessment_repo.create(
                company_id=company.id,
                prior_id=prior.id if prior is not None else None,
                external_id=notice.accession,
                kind=event.kind,
                form=notice.form,
                source_url=notice.index_url,
                score=result.score,
                direction=result.direction,
                headline=result.headline,
                matches=[
                    {
                        "topic": m.topic,
                        "direction": m.direction,
                        "severity": m.severity,
                        "matched_keywords": m.matched_keywords,
                        "already_priced": m.already_priced,
                    }
                    for m in result.matches
                ],
                surprises={
                    "eps_percent": result.eps_surprise_percent,
                    "revenue_percent": result.revenue_surprise_percent,
                    # Which basis the actual was taken on. A surprise shown
                    # without this is unauditable: consensus is quoted on an
                    # adjusted basis, so a GAAP comparison would be measuring
                    # an accounting difference.
                    "eps_basis": eps_basis,
                    "eps_actual": eps,
                    "revenue_actual": revenue,
                },
                amplifiers=result.amplifiers,
                occurred_at=event.occurred_at,
                latency_seconds=latency,
                scoring_ms=result.elapsed_ms,
            )
            if created is None:
                continue
            written += 1

            if result.is_notable:
                logger.warning(
                    "LIVE %s %s: %s (score %.2f, %.0fs after acceptance)",
                    company.ticker, notice.form, result.headline,
                    result.score, latency or 0.0,
                )
            else:
                logger.info(
                    "live %s %s: nothing watched matched (%.0fs after acceptance)",
                    company.ticker, notice.form, latency or 0.0,
                )

        return written
    finally:
        if owns_session:
            db.close()


def _loop(interval_seconds: int) -> None:
    logger.info("Filing watcher started, polling every %ds.", interval_seconds)

    cycles = 0
    assessed = 0
    failures = 0
    last_heartbeat = time.monotonic()

    while not _stop_event.is_set():
        try:
            assessed += run_cycle()
        except Exception:
            # Unattended loop: one bad cycle must never end the watch.
            failures += 1
            logger.exception("Watcher cycle failed.")
        cycles += 1

        now = time.monotonic()
        if now - last_heartbeat >= HEARTBEAT_SECONDS:
            logger.info(
                "Watcher alive: %d cycles, %d events assessed, %d failed cycles "
                "since the last report.",
                cycles, assessed, failures,
            )
            cycles = assessed = failures = 0
            last_heartbeat = now

        _stop_event.wait(interval_seconds)
    logger.info("Filing watcher stopped.")


def start_watcher() -> None:
    global _thread
    if not settings.watcher_enabled:
        logger.info("Filing watcher disabled.")
        return
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(
        target=_loop, args=(settings.watcher_interval_seconds,), daemon=True, name="filing-watcher"
    )
    _thread.start()


def stop_watcher() -> None:
    _stop_event.set()
