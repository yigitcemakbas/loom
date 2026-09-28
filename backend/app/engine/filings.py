"""Keeping the filing corpus deep enough for the engine to work on.

This exists because of a measured shortfall rather than a preference. Loom had
322 periodic filings across a thousand companies, and 36 of the 46 companies it
had actually read held exactly one. Everything downstream depended on that:

  * the risk diff compares a filing against its predecessor, so with one filing
    Loom's most trustworthy signal — deterministic, checkable against two
    documents — is unavailable by construction
  * the corpus therefore falls back on the types priority.py explicitly distrusts,
    quotes and tone reads, which is why every stored finding scored below 0.35
  * the incidence residual is measured per document, so a company with one
    document has a verdict resting on one document

Fetching filings is free. It needs no model, only SEC's rate limit, and the
adapter already downloads the submissions index in a single request — the old
path fetched that index and then threw away everything except one filing.

So this runs continuously rather than as a one-off migration. Two jobs in one:
companies below the target depth are filled in first, and once a company is at
target it is still revisited so a newly published 10-Q is picked up rather than
waiting for someone to notice. Both halves use the same bounded queue, so the
work is spread over runs instead of arriving as a burst SEC would refuse.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.document import RawDocument

logger = logging.getLogger(__name__)

# How many annual and quarterly reports a company should eventually hold.
#
# Four, because the risk diff needs a predecessor and a year-over-year comparison
# wants the same quarter twelve months back. Below two the diff cannot run at all;
# beyond four the marginal filing mostly restates the one before it.
TARGET_FILINGS = 4

# Documents fetched per scheduled run. Bounded because SEC's limit is about ten
# requests a second shared across everything Loom does, and because a job on a
# clock should leave the machine usable.
FETCHES_PER_RUN = 25

# How many companies one run will look at, even if most need nothing. Caps the
# submissions-index requests, which are cheap but not free.
COMPANIES_PER_RUN = 40


@dataclass
class BackfillResult:
    companies_examined: int = 0
    filings_stored: int = 0
    already_held: int = 0
    failed: int = 0
    remaining: int = 0

    def __str__(self) -> str:
        return (
            f"examined {self.companies_examined}, stored {self.filings_stored}, "
            f"already held {self.already_held}, failed {self.failed}, "
            f"{self.remaining} companies still below target"
        )


def filing_counts(db: Session) -> dict:
    """How many periodic filings each company holds, by company id."""
    rows = db.execute(
        select(RawDocument.company_id, func.count(RawDocument.id))
        .where(RawDocument.doc_subtype.in_(("10-K", "10-Q")))
        .group_by(RawDocument.company_id)
    ).all()
    return {str(company_id): count for company_id, count in rows}


def companies_needing_filings(
    db: Session, *, target: int = TARGET_FILINGS, limit: Optional[int] = None
) -> list[Company]:
    """Companies below the target depth, shallowest first.

    Shallowest first rather than largest first, because the marginal value of a
    filing is not linear: the second one turns the risk diff on, and the fourth
    only sharpens a comparison that already works. A company with none gains more
    from one filing than a company with three gains from its fourth.
    """
    from app.models.signal import Signal

    counts = filing_counts(db)
    companies = list(db.execute(select(Company)).scalars())
    below = [c for c in companies if counts.get(str(c.id), 0) < target]

    # Companies Loom has already read come first, and this ordering is the whole
    # point rather than a refinement.
    #
    # A company with findings and one filing is one download away from turning on
    # the risk diff — deterministic, checkable, the signal priority.py trusts most
    # — for a company that already has a verdict resting on the types it trusts
    # least. A company with no filings needs a download *and* a model call from a
    # twenty-a-day allowance before it produces anything at all.
    #
    # Ordering purely by fewest filings put all 960 unread companies ahead of the
    # 36 read ones, which meant the download that would have helped most arrived
    # last. Checked after the first run: none of the read companies had an
    # unanalysed filing waiting.
    read = {
        str(company_id) for (company_id,) in db.execute(
            select(Signal.company_id).distinct()
        ).all()
    }
    below.sort(key=lambda c: (
        str(c.id) not in read,
        counts.get(str(c.id), 0),
        c.sec_rank if c.sec_rank is not None else 10**9,
    ))
    return below[:limit] if limit else below


def companies_to_refresh(
    db: Session, *, target: int = TARGET_FILINGS, limit: int = 0
) -> list[Company]:
    """Companies already at target, oldest newest-filing first.

    This is the half that keeps the corpus current. A company at target still
    files every quarter, and without this the corpus would be complete once and
    then quietly go stale — which is the failure the price refresh already exists
    to prevent for prices.
    """
    if limit <= 0:
        return []
    counts = filing_counts(db)
    newest = dict(db.execute(
        select(RawDocument.company_id, func.max(RawDocument.published_at))
        .where(RawDocument.doc_subtype.in_(("10-K", "10-Q")))
        .group_by(RawDocument.company_id)
    ).all())
    at_target = [
        c for c in db.execute(select(Company)).scalars()
        if counts.get(str(c.id), 0) >= target
    ]
    at_target.sort(key=lambda c: (newest.get(c.id) is not None, newest.get(c.id)))
    return at_target[:limit]


def backfill_filings(
    db: Session,
    *,
    fetch_limit: int = FETCHES_PER_RUN,
    company_limit: int = COMPANIES_PER_RUN,
    target: int = TARGET_FILINGS,
    refresh_share: int = 5,
    companies: Optional[list[Company]] = None,
) -> BackfillResult:
    """Fetch and store periodic filings, bounded by `fetch_limit`.

    Stores documents only. Analysis costs model quota and is left to the coverage
    drip, which is already bounded against a free tier; separating the two means
    the corpus can get deep while the reading catches up at whatever rate the
    provider allows.
    """
    from app.ingestion.registry import DOCUMENT_ADAPTERS, _persist_document
    from app.ingestion.sec_edgar import SecEdgarAdapter
    from app.storage.blob_store import get_blob_store

    adapter = next(
        (a for a in DOCUMENT_ADAPTERS if isinstance(a, SecEdgarAdapter)), None
    )
    if adapter is None:
        logger.warning("Filing backfill: no SEC adapter registered.")
        return BackfillResult()

    blob_store = get_blob_store()
    counts = filing_counts(db)

    if companies is None:
        queue = companies_needing_filings(db, target=target, limit=company_limit)
        # Whatever room is left goes to keeping already-deep companies current.
        spare = max(0, company_limit - len(queue))
        queue += companies_to_refresh(db, target=target, limit=min(spare, refresh_share))
    else:
        queue = companies[:company_limit] if company_limit else companies

    result = BackfillResult(
        remaining=len(companies_needing_filings(db, target=target))
    )

    for company in queue:
        if result.filings_stored >= fetch_limit:
            break
        held = counts.get(str(company.id), 0)
        # A company at target is still checked, but only for something newer than
        # what it already has, which is one extra document at most.
        want = max(1, target - held)
        stored_urls = frozenset(
            url for (url,) in db.execute(
                select(RawDocument.source_url)
                .where(RawDocument.company_id == company.id)
                .where(RawDocument.source_url.is_not(None))
            ).all()
        )
        result.companies_examined += 1
        try:
            dtos = adapter.periodic_filings(
                company.ticker,
                limit=min(want, fetch_limit - result.filings_stored),
                skip_urls=stored_urls,
            )
        except Exception:
            logger.exception("Filing backfill: fetch failed for %s", company.ticker)
            result.failed += 1
            db.rollback()
            continue

        if not dtos:
            result.already_held += 1
            continue

        for dto in dtos:
            try:
                written = _persist_document(
                    dto, company_id=company.id, db=db, blob_store=blob_store
                )
            except Exception:
                logger.exception(
                    "Filing backfill: store failed for %s", company.ticker
                )
                db.rollback()
                result.failed += 1
                continue
            if written:
                result.filings_stored += 1
            else:
                result.already_held += 1
        db.commit()
        logger.info(
            "Filing backfill: %s now holds %d periodic filings.",
            company.ticker, held + sum(1 for _ in dtos),
        )

    result.remaining = len(companies_needing_filings(db, target=target))
    return result


__all__ = [
    "TARGET_FILINGS",
    "FETCHES_PER_RUN",
    "COMPANIES_PER_RUN",
    "BackfillResult",
    "backfill_filings",
    "companies_needing_filings",
    "companies_to_refresh",
    "filing_counts",
]
