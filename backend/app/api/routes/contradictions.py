"""Where Loom's own sources disagree about a company.

Served separately from the brief on purpose. A contradiction is not a component
of a verdict, it is a reason to distrust one, and folding it into the stance
would average away exactly the tension it exists to expose.
"""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import CompanyRepo, DbSession
from app.engine.contradiction import find_contradictions
from app.engine.price_context import moves_for
from app.engine.price_loader import load_benchmark, load_history
from app.models.factor import COMPOSITE_KEY, FactorScore
from app.repositories.brief_repository import BriefRepository
from app.repositories.signal_repository import SignalRepository

router = APIRouter(tags=["contradictions"])


class ContradictionOut(BaseModel):
    key: str
    headline: str
    says_better: str
    says_worse: str
    why_it_matters: str
    # The same explanation for a reader who does not already know the
    # vocabulary. Served alongside rather than instead of: the interface leads
    # with this one and keeps the other for whoever wants it.
    plain: str
    # The observation that would resolve the disagreement.
    settled_by: str
    signal_ids: list[str] = []
    factor_keys: list[str] = []


class ContradictionsOut(BaseModel):
    ticker: str
    contradictions: list[ContradictionOut]
    # What was compared, so an empty list reads as "these agree" rather than
    # as "Loom did not look".
    compared_sources: list[str]


@router.get("/companies/{ticker}/contradictions", response_model=ContradictionsOut)
def company_contradictions(ticker: str, company_repo: CompanyRepo, db: DbSession):
    company = company_repo.get_by_ticker(ticker.upper())
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")

    signals = SignalRepository(db).list_feed(company_id=company.id, limit=500)

    as_of = db.execute(select(func.max(FactorScore.as_of_date))).scalar()
    percentiles: dict[str, float] = {}
    if as_of is not None:
        rows = db.execute(
            select(FactorScore)
            .where(FactorScore.company_id == company.id)
            .where(FactorScore.as_of_date == as_of)
        ).scalars()
        for row in rows:
            if row.percentile is not None:
                percentiles[row.factor_key] = row.percentile

    brief = BriefRepository(db).latest_for(company.id)
    stance = brief.stance.value if brief else None

    # The market's response to the most recent filing Loom read. A third
    # independent assessment of the same disclosure, and the only one that
    # comes from outside Loom.
    # The most recent move that can actually be measured, rather than the most
    # recent finding's. A fortnight has to elapse before a fortnight's reaction
    # exists, so keying this on the newest finding answered None for every
    # company under active coverage.
    latest_move = None
    if signals:
        history = load_history(db, company.id)
        if history is not None:
            found = moves_for(signals, history, load_benchmark(db))
            # The most recent move large enough to be a reaction, not simply
            # the most recent move: a quiet procedural filing landing after a
            # repriced annual report would otherwise report that the market
            # shrugged at what Loom read.
            reactions = [m for m in found.values() if m.is_material]
            latest_move = max(reactions, key=lambda m: m.as_of) if reactions else None

    found = find_contradictions(signals, percentiles, stance=stance, move=latest_move)

    compared = []
    if signals:
        compared.append("filings and transcripts Loom has read")
    if percentiles:
        compared.append("reported financial statements")
    if COMPOSITE_KEY in percentiles and stance:
        compared.append("Loom's own verdict")
    if latest_move is not None:
        compared.append("what the share price did afterwards")

    return ContradictionsOut(
        ticker=company.ticker,
        contradictions=[ContradictionOut(**c.__dict__) for c in found],
        compared_sources=compared,
    )
