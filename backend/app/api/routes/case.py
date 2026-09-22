"""One company, as one argument.

Assembles what every other endpoint serves separately and hands it to
engine/case.py to rank. The route does the loading; the engine does the
judgement, and holds no session, so the ordering can be tested without a
database.
"""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.deps import CompanyRepo, DbSession
from app.api.routes.auth import OptionalUser
from app.engine.case import build_case
from app.engine.contradiction import find_contradictions
from app.engine.quant.crosssection import percentile_phrase
from app.engine.quant.factors import FACTORS_BY_KEY
from app.models.account import Position, User
from app.models.event_assessment import EventAssessment
from app.models.factor import COMPOSITE_KEY, FactorScore
from app.repositories.brief_repository import BriefRepository
from app.repositories.prior_repository import PriorRepository
from app.repositories.signal_repository import SignalRepository

router = APIRouter(tags=["case"])

# How many findings feed the case. The ranking decides what a reader sees, so
# the cap is about assembly cost rather than about presentation: beyond this,
# older findings cannot outrank what is already there.
MAX_FINDINGS = 60

# Matched filings older than this cannot be news, and the engine drops them
# anyway. Bounded here so the query does not read a year of history to discard
# most of it.
EVENT_WINDOW_DAYS = 30


class CasePointOut(BaseModel):
    key: str
    headline: str
    detail: str
    side: str
    weight: int
    source: str
    settles_it: str | None = None
    quote: str | None = None


class CaseFileOut(BaseModel):
    ticker: str
    name: str
    stance: str | None
    headline: str
    confidence: float
    points: list[CasePointOut]
    valuation: list[CasePointOut]
    gaps: list[str]
    held: bool
    # Surfaced separately from `points` so the interface cannot bury it. A page
    # showing only the case for a conclusion is a sales pitch.
    strongest_against: CasePointOut | None = None


class _FactorView:
    """The shape the case engine expects, built from a stored score.

    A small adapter rather than passing ORM rows in, so the engine keeps its
    promise not to know about the database.
    """

    def __init__(self, score: FactorScore):
        factor = FACTORS_BY_KEY.get(score.factor_key)
        self.key = score.factor_key
        self.percentile = score.percentile
        self.label = factor.label if factor else score.factor_key.replace("_", " ")
        self.meaning = factor.meaning if factor else ""
        self.source = factor.source if factor else "reported financial statements"
        self.phrase = percentile_phrase(score.percentile)


@router.get("/companies/{ticker}/case", response_model=CaseFileOut)
def company_case(
    ticker: str,
    company_repo: CompanyRepo,
    db: DbSession,
    user: User | None = OptionalUser,
):
    company = company_repo.get_by_ticker(ticker.upper())
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")

    findings = SignalRepository(db).list_feed(company_id=company.id, limit=MAX_FINDINGS)
    brief = BriefRepository(db).latest_for(company.id)
    prior = PriorRepository(db).latest_for(company.id)

    as_of = db.execute(select(func.max(FactorScore.as_of_date))).scalar()
    scores = []
    percentiles: dict[str, float] = {}
    if as_of is not None:
        scores = list(db.execute(
            select(FactorScore)
            .where(FactorScore.company_id == company.id)
            .where(FactorScore.as_of_date == as_of)
        ).scalars())
        percentiles = {
            s.factor_key: s.percentile for s in scores if s.percentile is not None
        }

    events = list(db.execute(
        select(EventAssessment)
        .where(EventAssessment.company_id == company.id)
        .where(EventAssessment.occurred_at >= datetime.now(timezone.utc) - timedelta(days=EVENT_WINDOW_DAYS))
        .where(EventAssessment.score >= 1.0)
        .order_by(EventAssessment.score.desc())
        .limit(5)
    ).scalars())

    contradictions = find_contradictions(
        findings, percentiles, stance=brief.stance.value if brief else None,
    )

    held = False
    if user is not None:
        held = db.execute(
            select(Position.id)
            .where(Position.user_id == user.id)
            .where(Position.company_id == company.id)
        ).scalars().first() is not None

    case = build_case(
        ticker=company.ticker,
        name=company.name,
        brief=brief,
        contradictions=contradictions,
        # The composite is excluded: it backtested at no better than chance
        # over 203 rebalances, and a case file is where a reader looks for
        # reasons rather than for every number Loom holds.
        factors=[_FactorView(s) for s in scores if s.factor_key != COMPOSITE_KEY],
        findings=findings,
        events=events,
        prior=prior,
        held=held,
    )

    strongest = case.strongest_against
    return CaseFileOut(
        ticker=case.ticker, name=case.name, stance=case.stance,
        headline=case.headline, confidence=case.confidence,
        points=[CasePointOut(**p.__dict__) for p in case.points],
        valuation=[CasePointOut(**p.__dict__) for p in case.valuation],
        gaps=case.gaps, held=case.held,
        strongest_against=CasePointOut(**strongest.__dict__) if strongest else None,
    )
