"""Closing the coverage gap a few companies at a time.

Loom tracks a hundred and thirty companies and, for most of its life, had
anything to say about seven. Every other page reported its own emptiness
politely, which is a hundred and twenty-three promises the product was failing
to keep, and it was the loudest thing in the interface.

The gap had two different causes and they need different fixes.

**Priors were purely quota-bound.** They apply to every focus and watch company,
so all hundred and thirty could have one; only thirteen did, because building
them was a command somebody had to remember to run and the free tier refuses a
long burst. A drip fixes that: a few per run, ordered so the uncovered come
first, stopping the moment the provider says no and picking up where it left
off next time.

**Reading was tier-bound, which is not the same thing.** A watch-tier company
never has its documents ingested at all, so it cannot have findings however
much quota is available. Promoting everything to focus would be the obvious fix
and the wrong one: focus companies are re-ingested and re-analysed every six
hours, and a hundred and thirty of those would exhaust a free tier in an
afternoon and keep doing so forever.

So watch companies get a **one-shot read** instead: their newest annual or
quarterly filing, fetched and analysed once, which is enough to produce findings
and therefore a verdict. It costs a bounded handful of calls per company, once,
rather than a recurring bill. A company that turns out to matter can be promoted
to focus deliberately.

Both drips are ordered by need rather than alphabetically. A quota-limited run
that always starts at A never reaches the end of the alphabet.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.engine.llm_client import LLMUnavailableError
from app.models.company import Company, CompanyTier
from app.models.document import RawDocument
from app.models.prior import CompanyPrior
from app.models.signal import Signal

logger = logging.getLogger(__name__)

# How many companies one drip run may touch.
#
# These were chosen when one exhausted Gemini tier was the whole ceiling, and
# two reads every two hours is 24 a day. That described a real constraint then
# and does not now: the measured backlog is 6,068 documents at a mean prepared
# payload of 9,760 tokens, about 59M input tokens in total, which is hours of
# provider time rather than months.
#
# Raising this is safe because the run self-limits. A drip stops cleanly the
# moment every provider refuses, so a figure set too high ends the run early
# instead of burning an allowance in a burst — the limit is an upper bound on
# work attempted, not a promise to attempt it.
#
# Eight reads is roughly ten minutes of wall time, comfortably inside the two
# hour interval, and clears the 121 companies still queued in about a day.
PRIORS_PER_RUN = 3
READS_PER_RUN = 8

# The filings a one-shot read looks at. Annual first, because a 10-K carries
# the risk factors and the full-year picture that a verdict actually rests on.
ONE_SHOT_FORMS = ("10-K", "10-Q")

# How many filings a company should eventually have been read, before quota
# goes to breadth instead.
#
# Depth was worth more than the drip assumed. A company read once has all its
# findings inside a single document, and the clustering correction weights each
# of them by one over the square root of that document's finding count: nine
# findings from one filing are worth 0.333 each, and the same nine spread over
# three filings are worth 0.577. So a second read roughly doubles a company's
# evidence strength without extracting a single new fact, and a company with one
# document can never reach a strong verdict at all because that needs two
# independent kinds of source. Measured on the corpus, the median covered
# company had exactly one document and the only company Loom had a verdict for
# was the one with five.
DEPTH_TARGET_DOCUMENTS = 3

# A prior older than this is rebuilt even if one exists, so coverage does not
# quietly become a set of standing views describing last year's company.
STALE_PRIOR_DAYS = 120


@dataclass
class DripResult:
    covered: int = 0
    failed: int = 0
    # Companies passed over because no available provider had a lane wide
    # enough for their filing. Separate from `failed`: nothing went wrong and
    # the company is still a candidate next run.
    skipped: int = 0
    # True when the provider refused. The caller stops rather than grinding
    # through the rest producing the same error once per company.
    exhausted: bool = False
    remaining: int = 0


def companies_needing_priors(db: Session, *, now: Optional[datetime] = None) -> list[Company]:
    """Focus and watch companies with no prior, or a stale one, neediest first.

    Ordered so a quota-limited run makes progress everywhere rather than
    perfecting the start of the alphabet. A company with no prior at all scores
    every filing zero, which is indistinguishable from a quiet week, so those
    come before any refresh.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(days=STALE_PRIOR_DAYS)

    latest = (
        select(
            CompanyPrior.company_id.label("company_id"),
            func.max(CompanyPrior.generated_at).label("generated_at"),
        )
        .group_by(CompanyPrior.company_id)
        .subquery()
    )

    rows = db.execute(
        select(Company, latest.c.generated_at)
        .outerjoin(latest, latest.c.company_id == Company.id)
        .where(Company.tier.in_([CompanyTier.FOCUS, CompanyTier.WATCH]))
        .order_by(Company.ticker)
    ).all()

    missing = [c for c, generated in rows if generated is None]
    stale = [
        c for c, generated in rows
        if generated is not None and _aware(generated) < cutoff
    ]
    # Never-covered first, then oldest-first among the stale.
    return missing + stale


def companies_needing_a_read(db: Session) -> list[Company]:
    """Watch-tier companies Loom has never read, least-covered sector first.

    Focus companies are excluded: they are already on the continuous path, and
    a drip competing with it would spend quota re-reading what the scheduled
    refresh is about to read anyway.

    **Ordered by how little Loom has read of the company's sector**, not by
    ticker. The module docstring already promised ordering by need, and the
    prior drip delivers it; this path did not, and the consequence was
    measurable rather than cosmetic.

    Reading alphabetically inside a quota that covers two companies per run
    produces a corpus shaped like the alphabet. What it actually produced was a
    corpus shaped like one sector: of the thirty companies Loom has read,
    thirteen are Technology and two are Industrials, so every cross-sectional
    measurement over findings was really a measurement of Technology. The
    sector-drift test failed on exactly this, twice, and the second failure
    survived a fivefold increase in the price universe because the *disclosures*
    were still 50 to 90 percent one sector.

    So the ordering asks which sector Loom knows least about and goes there
    first. Within a sector, the largest companies come first: they file more,
    are written about more, and their disclosures reach further, so they are
    worth more per unit of quota than a micro-cap that files once a year.
    """
    with_findings = select(Signal.company_id).distinct().subquery()
    rows = list(db.execute(
        select(Company)
        .where(Company.tier == CompanyTier.WATCH)
        .where(Company.id.not_in(select(with_findings.c.company_id)))
    ).scalars())

    # How many companies Loom has already read in each sector. Counted over the
    # whole universe rather than the candidates, because the question is what
    # Loom knows, not what is left.
    read_counts = dict(db.execute(
        select(Company.sector, func.count(Company.id.distinct()))
        .join(Signal, Signal.company_id == Company.id)
        .group_by(Company.sector)
    ).all())

    return read_order(rows, read_counts)


# Unranked companies sort last rather than first. `sec_rank` is absent for a
# company added by hand rather than seeded from SEC's directory, and treating a
# missing size as the largest size would put every hand-added ticker at the
# front of the queue.
_UNRANKED = 10**9


def read_order(companies: list, read_counts: dict) -> list:
    """Order candidates by how little Loom knows about their sector.

    Pure, and separated from the query for that reason: the ordering is the
    part with a judgement in it, and it should be testable without a database.
    """
    def rank(company) -> tuple:
        return (
            read_counts.get(getattr(company, "sector", None), 0),
            # Largest first within a sector. They file more, are written about
            # more, and their disclosures reach further, so they are worth more
            # per unit of quota than a micro-cap that files once a year.
            getattr(company, "sec_rank", None) if getattr(company, "sec_rank", None) is not None else _UNRANKED,
            getattr(company, "ticker", ""),
        )

    return sorted(companies, key=rank)


def drip_priors(
    db: Session, *, limit: int = PRIORS_PER_RUN, now: Optional[datetime] = None
) -> DripResult:
    """Build priors for the neediest companies, stopping cleanly on quota."""
    from app.engine.prior import build_prior

    pending = companies_needing_priors(db, now=now)
    result = DripResult(remaining=len(pending))

    for company in pending[:limit]:
        try:
            prior = build_prior(company.ticker, db)
        except LLMUnavailableError as exc:
            # Applies to everything left, so stop rather than log the same
            # failure once per remaining company.
            logger.info("Prior drip stopping: %s", exc)
            result.exhausted = True
            break
        except Exception:
            logger.exception("Prior drip failed for %s", company.ticker)
            result.failed += 1
            continue

        if prior is None:
            # Nothing to build from. Counted as neither covered nor failed:
            # the company will be offered again next run, and if it is still
            # empty nothing is lost.
            continue
        result.covered += 1
        logger.info(
            "Prior drip: %s armed with %d watch items.",
            company.ticker, len(prior.watch_items or []),
        )

    result.remaining = max(0, result.remaining - result.covered)
    return result


def companies_needing_depth(db: Session) -> list[Company]:
    """Companies Loom has read once and should read again, shallowest first.

    The counterpart to `companies_needing_a_read`, which only ever looks for
    companies with nothing at all. Breadth alone left every covered company at a
    single document, which is the worst case for both of the quantities a verdict
    rests on: the clustering correction is harshest inside one document, and a
    strong verdict requires two independent sources, so a one-document company is
    capped regardless of what its filing said.
    """
    counted = (
        select(Signal.company_id.label("cid"),
               func.count(func.distinct(Signal.source_document_id)).label("docs"))
        .where(Signal.source_document_id.is_not(None))
        .group_by(Signal.company_id)
        .subquery()
    )
    rows = list(db.execute(
        select(Company, counted.c.docs)
        .join(counted, counted.c.cid == Company.id)
        .where(counted.c.docs < DEPTH_TARGET_DOCUMENTS)
        .order_by(counted.c.docs.asc(), Company.sec_rank.asc().nullslast())
    ).all())
    return [company for company, _docs in rows]


def _unanalysed_filing(db: Session, company: Company) -> Optional[RawDocument]:
    """A stored annual or quarterly report for this company with no findings yet.

    Newest first, and deliberately only documents already stored: the depth pass
    spends its quota on analysis, which is the expensive half, rather than on
    fetching a corpus it may not get to read.
    """
    read = select(Signal.source_document_id).where(Signal.company_id == company.id)
    return db.execute(
        select(RawDocument)
        .where(RawDocument.company_id == company.id)
        .where(RawDocument.doc_subtype.in_(ONE_SHOT_FORMS))
        .where(RawDocument.id.not_in(read))
        .order_by(RawDocument.published_at.desc())
        .limit(1)
    ).scalars().first()


def _some_provider_is_up() -> bool:
    """Whether any provider still has an allowance, regardless of its width."""
    from app.engine.llm_client import LLMUnavailableError as _Unavailable, get_llm_client

    try:
        return bool(get_llm_client().available)
    except _Unavailable:
        # Nothing configured at all. Stopping is right.
        return False


def drip_reads(db: Session, *, limit: int = READS_PER_RUN) -> DripResult:
    """Read one filing for companies Loom has never read.

    Bounded by construction: the newest annual or quarterly report and nothing
    else. Enough to produce findings and a verdict, at a one-time cost rather
    than signing the company up for analysis every six hours.
    """
    from app.engine.pipeline import analyze_document
    from app.ingestion.registry import DOCUMENT_ADAPTERS, _persist_document
    from app.storage.blob_store import get_blob_store

    pending = companies_needing_a_read(db)
    deeper = companies_needing_depth(db)
    result = DripResult(remaining=len(pending) + len(deeper))
    blob_store = get_blob_store()

    # Split, rather than spending the whole quota on first reads. Depth is
    # worth more per call on the companies already covered, and breadth is what
    # keeps the genre norms from being a measurement of one sector, so neither
    # is allowed to starve the other. Whichever queue is empty yields its share.
    depth_share = limit // 2 if pending else limit
    breadth_share = limit - depth_share
    if not deeper:
        breadth_share, depth_share = limit, 0
    queue = [(c, False) for c in pending[:breadth_share]]
    queue += [(c, True) for c in deeper[:depth_share]]

    for company, is_depth in queue:
        try:
            document = (
                _unanalysed_filing(db, company) if is_depth
                else _newest_filing(db, company)
            )
            if document is None and not is_depth:
                document = _fetch_one_filing(
                    db, company, DOCUMENT_ADAPTERS, blob_store, _persist_document
                )
            if document is None:
                logger.info("Read drip: no filing available for %s.", company.ticker)
                continue

            written = analyze_document(document.id, db)
        except LLMUnavailableError as exc:
            # Two different failures arrive here and only one is a reason to
            # stop. If some provider is still up, the call failed because no
            # available lane was wide enough for *this* filing — a property of
            # the document, not the account — and the next company's filing may
            # fit perfectly well. Breaking on that meant one oversized 10-K
            # ended the whole run: a drip raised to eight reads still covered
            # exactly one company before giving up.
            if _some_provider_is_up():
                logger.info(
                    "Read drip: skipping %s, no lane wide enough (%s)",
                    company.ticker, str(exc)[:120],
                )
                result.skipped += 1
                continue
            logger.info("Read drip stopping: %s", exc)
            result.exhausted = True
            break
        except Exception:
            logger.exception("Read drip failed for %s", company.ticker)
            result.failed += 1
            continue

        if written:
            result.covered += 1
            logger.info(
                "Read drip: %s %s (%s), %d findings.",
                company.ticker, "read deeper" if is_depth else "read once",
                document.doc_subtype, written,
            )

    result.remaining = max(0, result.remaining - result.covered)
    return result


def _newest_filing(db: Session, company: Company) -> Optional[RawDocument]:
    """An annual or quarterly report already stored for this company."""
    return db.execute(
        select(RawDocument)
        .where(RawDocument.company_id == company.id)
        .where(RawDocument.doc_subtype.in_(ONE_SHOT_FORMS))
        .order_by(RawDocument.published_at.desc())
        .limit(1)
    ).scalars().first()


def _fetch_one_filing(
    db: Session, company: Company, adapters, blob_store, persist
) -> Optional[RawDocument]:
    """Fetch and store exactly one filing for a company that has none.

    Deliberately not a call into the ordinary ingest path, which is gated on
    tier and would fetch nothing for a watch company. This asks the SEC adapter
    directly, keeps the newest annual or quarterly report, and discards the
    rest, so a watch company gains one document rather than a corpus.
    """
    for adapter in adapters:
        if "edgar" not in adapter.source_name:
            continue
        try:
            dtos = list(adapter.fetch(company.ticker, since=None))
        except Exception:
            logger.exception("Read drip: fetch failed for %s", company.ticker)
            return None

        candidates = [d for d in dtos if getattr(d, "doc_subtype", None) in ONE_SHOT_FORMS]
        if not candidates:
            return None
        # Annual before quarterly, then newest: a 10-K carries the risk factors
        # and full-year picture a verdict actually rests on.
        candidates.sort(
            key=lambda d: (d.doc_subtype != "10-K", -(d.published_at.timestamp() if d.published_at else 0))
        )
        persist(candidates[0], company_id=company.id, db=db, blob_store=blob_store)
        db.commit()
        return _newest_filing(db, company)
    return None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


__all__ = [
    "DEPTH_TARGET_DOCUMENTS",
    "ONE_SHOT_FORMS",
    "companies_needing_depth",
    "PRIORS_PER_RUN",
    "READS_PER_RUN",
    "STALE_PRIOR_DAYS",
    "DripResult",
    "companies_needing_a_read",
    "companies_needing_priors",
    "drip_priors",
    "drip_reads",
]
