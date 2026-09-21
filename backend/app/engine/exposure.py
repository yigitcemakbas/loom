"""Who moves when someone else files.

The insight this module exists for is that the company an event is *about* is
often not the company the event is most tradeable in. NVIDIA's earnings call
sets expectations for chip pricing, datacenter buildout and memory demand, so
the names that re-rate on it include contract manufacturers, memory makers and
GPU clouds that reported nothing at all that day. Loom saw the filing and had
nothing to say about any of them.

Exposure is inferred from filings rather than declared. A company that depends
on another says so in its own risk factors and business description, because it
is required to: material dependencies are disclosable. So counting how often
one company's filings name another gives a dependency graph for free, built
from primary sources, with no hand-maintained sector map to go stale.

**The direction is the part to get right.** The edge runs from dependent to
hub. AMD naming NVIDIA a hundred and thirty-six times means AMD is exposed to
NVIDIA; NVIDIA barely needs to name AMD. Inverting it would propagate every
event to precisely the wrong companies.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.ingestion.edgar_fts import EdgarFullTextSearch, core_name
from app.models.exposure import CompanyExposure
from app.repositories.company_repository import CompanyRepository

logger = logging.getLogger(__name__)

# Below this, a mention is incidental rather than a dependency. Calibrated
# against the tracked universe, where semiconductor names mention NVIDIA in
# 87 to 136 filings while unrelated large caps manage nought to six: the gap
# is wide enough that the exact cut matters much less than having one.
MIN_MENTIONS = 8

# Most exposed companies per hub. An event that supposedly moves forty
# positions is not a read, it is a market call.
MAX_DEPENDENTS = 12


@dataclass
class Exposure:
    ticker: str
    company_id: object
    mention_count: int


def build_exposures(db: Session, hub_tickers: Optional[list[str]] = None) -> int:
    """Rebuild the dependency graph. Returns the number of edges written.

    One request per hub, because the aggregation EDGAR returns alongside a
    search already contains a per-filer count. The naive alternative, asking
    about each pair, is quadratic and would be seventeen thousand requests for
    a universe this size.
    """
    company_repo = CompanyRepository(db)
    companies = company_repo.list_all()
    by_cik = {c.cik: c for c in companies if c.cik}
    if not by_cik:
        return 0

    hubs = (
        [c for c in companies if c.ticker in set(hub_tickers)]
        if hub_tickers
        else [c for c in companies if c.cik]
    )

    search = EdgarFullTextSearch()
    written = 0

    for hub in hubs:
        name = core_name(hub.name)
        if not name:
            continue

        counts = search.filer_counts(f'"{name}"')
        if not counts:
            continue

        ranked = sorted(counts.items(), key=lambda kv: -kv[1])
        kept = 0
        for cik, mentions in ranked:
            if kept >= MAX_DEPENDENTS:
                break
            if mentions < MIN_MENTIONS:
                # Ranked descending, so everything after this is smaller too.
                break

            dependent = by_cik.get(cik)
            if dependent is None or dependent.id == hub.id:
                # Self-mentions dominate every result: a company names itself
                # in all of its own filings.
                continue

            existing = db.execute(
                select(CompanyExposure).where(
                    CompanyExposure.dependent_company_id == dependent.id,
                    CompanyExposure.hub_company_id == hub.id,
                )
            ).scalars().first()

            if existing is None:
                db.add(
                    CompanyExposure(
                        dependent_company_id=dependent.id,
                        hub_company_id=hub.id,
                        mention_count=mentions,
                    )
                )
            else:
                existing.mention_count = mentions
                existing.computed_at = datetime.now(timezone.utc)
            kept += 1
            written += 1

        db.commit()
        logger.info("Exposure graph: %s has %d tracked dependents.", hub.ticker, kept)

    return written


def dependents_of(db: Session, hub_company_id, limit: int = MAX_DEPENDENTS) -> list[Exposure]:
    """Tracked companies that move when this one does, strongest first.

    This is the fast-path lookup: one indexed query at event time, no network
    and no model, matching the cost discipline of everything else on that path.
    """
    rows = db.execute(
        select(CompanyExposure)
        .where(CompanyExposure.hub_company_id == hub_company_id)
        .order_by(CompanyExposure.mention_count.desc())
        .limit(limit)
    ).scalars().all()

    company_repo = CompanyRepository(db)
    out: list[Exposure] = []
    for row in rows:
        company = company_repo.get_by_id(row.dependent_company_id)
        if company is not None:
            out.append(Exposure(company.ticker, company.id, row.mention_count))
    return out
