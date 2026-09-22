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

# How many companies one drip run may touch. Small on purpose: the point is to
# make steady progress inside a quota that refuses bursts, not to race through
# the universe and then fail for a day. At this rate the remaining priors are
# covered in a few days of ordinary running.
PRIORS_PER_RUN = 3
READS_PER_RUN = 2

# The filings a one-shot read looks at. Annual first, because a 10-K carries
# the risk factors and the full-year picture that a verdict actually rests on.
ONE_SHOT_FORMS = ("10-K", "10-Q")

# A prior older than this is rebuilt even if one exists, so coverage does not
# quietly become a set of standing views describing last year's company.
STALE_PRIOR_DAYS = 120


@dataclass
class DripResult:
    covered: int = 0
    failed: int = 0
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
    """Watch-tier companies Loom has never read, with documents worth reading.

    Focus companies are excluded: they are already on the continuous path, and
    a drip competing with it would spend quota re-reading what the scheduled
    refresh is about to read anyway.
    """
    with_findings = select(Signal.company_id).distinct().subquery()
    rows = db.execute(
        select(Company)
        .where(Company.tier == CompanyTier.WATCH)
        .where(Company.id.not_in(select(with_findings.c.company_id)))
        .order_by(Company.ticker)
    ).scalars()
    return list(rows)


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
    result = DripResult(remaining=len(pending))
    blob_store = get_blob_store()

    for company in pending[:limit]:
        try:
            document = _newest_filing(db, company)
            if document is None:
                document = _fetch_one_filing(
                    db, company, DOCUMENT_ADAPTERS, blob_store, _persist_document
                )
            if document is None:
                logger.info("Read drip: no filing available for %s.", company.ticker)
                continue

            written = analyze_document(document.id, db)
        except LLMUnavailableError as exc:
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
                "Read drip: %s read once (%s), %d findings.",
                company.ticker, document.doc_subtype, written,
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
    "ONE_SHOT_FORMS",
    "PRIORS_PER_RUN",
    "READS_PER_RUN",
    "STALE_PRIOR_DAYS",
    "DripResult",
    "companies_needing_a_read",
    "companies_needing_priors",
    "drip_priors",
    "drip_reads",
]
