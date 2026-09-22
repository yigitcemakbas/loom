"""Score the whole universe in one pass.

Cross-sectional ranking is why this is a universe-wide operation rather than a
per-company one. A percentile is a statement about every company measured on
the same day, so the run has to hold them all at once; scoring companies one at
a time and ranking later would rank each against a different universe.

Needs no model, no network and no quota. The inputs are figures already in the
database, and the whole pass is arithmetic, which makes this the part of Loom
that covers the entire universe rather than the handful of companies deep
reading has reached.
"""

import logging
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.quant.composite import build_composite, build_f_score
from app.engine.quant.crosssection import Ranked, rank_universe
from app.engine.quant.factors import FACTORS, FactorValue, compute_all
from app.engine.quant.sectors import comparable_group
from app.engine.quant.series import FactSeries, observations_from_facts
from app.models.company import Company
from app.models.factor import COMPOSITE_KEY, FactorScore
from app.models.structured_fact import FactType, StructuredFact

logger = logging.getLogger(__name__)

# A company that has not filed in this long is not scored. Its last figures are
# still in the database and would still compute, which is the problem: a
# two-year-old balance sheet ranked against current filers produces a
# confident-looking percentile about a company nobody has heard from.
MAX_STALENESS_DAYS = 400


@dataclass
class UniverseScores:
    as_of: date
    # ticker -> factor key -> ranked reading
    ranked: dict[str, dict[str, Ranked]]
    # ticker -> factor key -> the filed figures behind it
    raw: dict[str, dict[str, FactorValue]]
    # ticker -> the pool it was ranked inside ("universe" or a sector name).
    # Reported because "worst decile" means something different in a pool of
    # twenty-two banks than in a universe of a hundred and twenty.
    group: dict[str, str]
    skipped_stale: list[str]
    skipped_thin: list[str]


def _series_by_company(db: Session, companies: list[Company]) -> dict[str, FactSeries]:
    """One query for the whole universe, then split in memory.

    Per-company queries here would be a hundred and thirty round trips for data
    that is read in full anyway.
    """
    ids = [c.id for c in companies]
    if not ids:
        return {}
    facts = db.execute(
        select(StructuredFact)
        .where(StructuredFact.company_id.in_(ids))
        .where(StructuredFact.fact_type == FactType.FUNDAMENTAL)
    ).scalars().all()

    grouped: dict[str, list] = {}
    for fact in facts:
        grouped.setdefault(str(fact.company_id), []).append(fact)

    out: dict[str, FactSeries] = {}
    for company in companies:
        rows = grouped.get(str(company.id), [])
        out[company.ticker] = FactSeries(observations_from_facts(rows))
    return out


def score_universe(
    db: Session, *, as_of: Optional[date] = None, tickers: Optional[Iterable[str]] = None
) -> UniverseScores:
    """Compute every factor for every company, then rank them against each other."""
    as_of = as_of or date.today()

    query = select(Company)
    if tickers:
        query = query.where(Company.ticker.in_([t.upper() for t in tickers]))
    companies = list(db.execute(query).scalars())

    series_by_ticker = _series_by_company(db, companies)

    raw: dict[str, dict[str, FactorValue]] = {}
    skipped_stale: list[str] = []
    skipped_thin: list[str] = []

    for ticker, series in series_by_ticker.items():
        visible = series.as_of(as_of)
        newest = visible.newest_filing
        if newest is None:
            skipped_thin.append(ticker)
            continue
        if (as_of - newest).days > MAX_STALENESS_DAYS:
            skipped_stale.append(ticker)
            continue
        values = compute_all(visible)
        if not values:
            skipped_thin.append(ticker)
            continue
        raw[ticker] = values

    # Which pool each company is ranked inside. Banks are ranked against banks
    # and everyone else against the universe, because a ruler built for
    # operating companies puts every bank in the bottom decile on profitability
    # and leverage regardless of how the bank is doing. The first universe-wide
    # run of this engine returned eight banks as the eight weakest companies in
    # the database, in order, which is what this exists to prevent.
    sector_by_ticker = {c.ticker: c.sector for c in companies}
    sector_counts: dict[str, int] = {}
    for ticker in raw:
        sector = sector_by_ticker.get(ticker)
        if sector:
            sector_counts[sector] = sector_counts.get(sector, 0) + 1

    group: dict[str, str] = {}
    for ticker in raw:
        pool = comparable_group(sector_by_ticker.get(ticker), sector_counts)
        if pool is not None:
            group[ticker] = pool

    # The cross-section: one ranking per factor per pool. Done after the loop
    # because a rank cannot exist until every company has been measured.
    ranked: dict[str, dict[str, Ranked]] = {t: {} for t in raw}
    pools = sorted(set(group.values()))
    for factor in FACTORS:
        for pool in pools:
            members = [t for t in raw if group.get(t) == pool and factor.key in raw[t]]
            values = {t: raw[t][factor.key].value for t in members}
            for ticker, rank in rank_universe(
                values, higher_is_better=factor.higher_is_better, key=factor.key
            ).items():
                ranked[ticker][factor.key] = rank

    unrankable = sorted(t for t in raw if t not in group)
    logger.info(
        "Scored %d companies as of %s (%d too stale, %d without usable figures, "
        "%d with no comparable peer group).",
        len(raw), as_of, len(skipped_stale), len(skipped_thin), len(unrankable),
    )
    return UniverseScores(
        as_of=as_of, ranked=ranked, raw=raw, group=group,
        skipped_stale=skipped_stale, skipped_thin=skipped_thin,
    )


def persist(db: Session, scores: UniverseScores) -> int:
    """Write one row per company per factor, plus the folded composite.

    Rows for the same company, date and factor are replaced rather than
    duplicated, so a re-run corrects a day instead of stacking versions of it.
    """
    companies = {
        c.ticker: c.id for c in db.execute(select(Company)).scalars()
    }

    db.query(FactorScore).filter(FactorScore.as_of_date == scores.as_of).delete(
        synchronize_session=False
    )

    written = 0
    for ticker, factors in scores.raw.items():
        company_id = companies.get(ticker)
        if company_id is None:
            continue
        ranks = scores.ranked.get(ticker, {})

        for key, value in factors.items():
            rank = ranks.get(key)
            db.add(
                FactorScore(
                    company_id=company_id,
                    as_of_date=scores.as_of,
                    factor_key=key,
                    value=value.value,
                    percentile=rank.percentile if rank else None,
                    universe_size=rank.universe_size if rank else None,
                    inputs={**value.inputs, "peer_group": scores.group.get(ticker)},
                )
            )
            written += 1

        composite = build_composite(ranks)
        if composite is None:
            # No row at all rather than a null-scored one. A company that could
            # not be scored must be absent from a ranking of scored companies,
            # not present with a blank.
            continue
        health = build_f_score({k: v.value for k, v in factors.items()})
        db.add(
            FactorScore(
                company_id=company_id,
                as_of_date=scores.as_of,
                factor_key=COMPOSITE_KEY,
                value=composite.score,
                percentile=composite.score,
                universe_size=len(scores.raw),
                inputs={
                    "peer_group": scores.group.get(ticker),
                    "factor_count": composite.factor_count,
                    "factors_used": composite.factors_used,
                    "extremes": composite.extremes,
                    "health_passed": health.passed,
                    "health_available": health.available,
                    "health_failed": health.failed_tests,
                    "health_passed_tests": health.passed_tests,
                },
            )
        )
        written += 1

    db.commit()
    return written


__all__ = ["MAX_STALENESS_DAYS", "UniverseScores", "persist", "score_universe"]
