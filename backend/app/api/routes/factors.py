"""Quantitative factor scores, per company and across the universe.

Served with the factor's meaning and source attached rather than as bare
numbers. "accruals: 0.0599" is a complete answer to a professional and no
answer at all to anyone else, and the whole point of scoring the universe is
that the reading becomes interpretable: worst one percent of the companies
Loom tracks, on a measure that says how much of the profit did not arrive as
cash.
"""

from datetime import date

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import CompanyRepo, DbSession
from app.engine.quant.crosssection import percentile_phrase
from app.engine.quant.factors import FACTORS_BY_KEY
from app.models.company import Company
from app.models.factor import COMPOSITE_KEY, FactorScore

router = APIRouter(tags=["factors"])


class FactorOut(BaseModel):
    key: str
    label: str
    meaning: str
    source: str
    # True when a LOW raw value is the good one. Several of the best documented
    # factors are inverted, and a client that renders the raw number without
    # this will colour them backwards.
    higher_is_better: bool
    value: float
    percentile: float | None
    percentile_phrase: str | None
    universe_size: int | None
    # Top or bottom decile: the readings that justify surfacing a company.
    is_extreme: bool
    inputs: dict


class HealthOut(BaseModel):
    """Piotroski's fundamental tests, over those this database can run.

    `available` is always reported with `passed`, because 4 of 7 and 4 of 5 are
    different statements and the denominator is a property of Loom's data
    rather than of the company.
    """

    passed: int
    available: int
    failed: list[str]


class CompanyFactorsOut(BaseModel):
    ticker: str
    as_of: date
    # Null when too few factors could be computed to fold one honestly.
    composite: float | None
    composite_phrase: str
    factor_count: int
    health: HealthOut | None
    factors: list[FactorOut]


class LeaderboardRow(BaseModel):
    ticker: str
    name: str
    composite: float
    factor_count: int
    health_passed: int | None
    health_available: int | None
    extremes: list[str]


def _latest_date(db) -> date | None:
    return db.execute(select(func.max(FactorScore.as_of_date))).scalar()


def _to_factor_out(row: FactorScore) -> FactorOut | None:
    factor = FACTORS_BY_KEY.get(row.factor_key)
    if factor is None:
        return None
    percentile = row.percentile
    return FactorOut(
        key=row.factor_key,
        label=factor.label,
        meaning=factor.meaning,
        source=factor.source,
        higher_is_better=factor.higher_is_better,
        value=row.value,
        percentile=percentile,
        percentile_phrase=percentile_phrase(percentile),
        universe_size=row.universe_size,
        is_extreme=percentile is not None and (percentile <= 0.1 or percentile >= 0.9),
        inputs=row.inputs or {},
    )


@router.get("/companies/{ticker}/factors", response_model=CompanyFactorsOut)
def company_factors(ticker: str, company_repo: CompanyRepo, db: DbSession):
    company = company_repo.get_by_ticker(ticker.upper())
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")

    as_of = _latest_date(db)
    if as_of is None:
        raise HTTPException(status_code=404, detail="No factor scores have been computed yet.")

    rows = list(db.execute(
        select(FactorScore)
        .where(FactorScore.company_id == company.id)
        .where(FactorScore.as_of_date == as_of)
    ).scalars())
    if not rows:
        raise HTTPException(
            status_code=404,
            detail=f"{company.ticker} has not filed enough for Loom to score it.",
        )

    composite_row = next((r for r in rows if r.factor_key == COMPOSITE_KEY), None)
    factors = [f for f in (_to_factor_out(r) for r in rows) if f is not None]
    # Worst readings first. A reader scanning a list stops near the top, and
    # what a decision most needs is the thing arguing against it.
    factors.sort(key=lambda f: (f.percentile if f.percentile is not None else 1.0))

    health = None
    composite = None
    phrase = "Not enough reported figures to score this company."
    factor_count = len(factors)
    if composite_row is not None:
        details = composite_row.inputs or {}
        composite = composite_row.value
        factor_count = details.get("factor_count", factor_count)
        available = details.get("health_available")
        if available:
            health = HealthOut(
                passed=details.get("health_passed", 0),
                available=available,
                failed=details.get("health_failed", []),
            )
        from app.engine.quant.composite import Composite, composite_phrase

        phrase = composite_phrase(
            Composite(score=composite, factor_count=factor_count,
                      factors_used=details.get("factors_used", []),
                      extremes=details.get("extremes", []))
        )

    return CompanyFactorsOut(
        ticker=company.ticker, as_of=as_of, composite=composite,
        composite_phrase=phrase, factor_count=factor_count,
        health=health, factors=factors,
    )


@router.get("/factors/leaderboard", response_model=list[LeaderboardRow])
def leaderboard(
    db: DbSession,
    limit: int = Query(200, ge=1, le=400),
):
    """Every company Loom could score, strongest reported numbers first.

    This is the one view in the product that covers the whole universe rather
    than the companies deep reading has reached, because its inputs are figures
    every filer is required to publish.
    """
    as_of = _latest_date(db)
    if as_of is None:
        return []

    rows = db.execute(
        select(FactorScore, Company)
        .join(Company, Company.id == FactorScore.company_id)
        .where(FactorScore.as_of_date == as_of)
        .where(FactorScore.factor_key == COMPOSITE_KEY)
        .order_by(FactorScore.value.desc())
        .limit(limit)
    ).all()

    out: list[LeaderboardRow] = []
    for score, company in rows:
        details = score.inputs or {}
        out.append(
            LeaderboardRow(
                ticker=company.ticker,
                name=company.name,
                composite=score.value,
                factor_count=details.get("factor_count", 0),
                health_passed=details.get("health_passed"),
                health_available=details.get("health_available"),
                extremes=details.get("extremes", []),
            )
        )
    return out
