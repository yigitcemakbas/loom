"""Submitting and reading agent decisions for the paired trial.

The endpoint an agent calls after it has decided. Deliberately strict about
one thing: a decision without a rationale is refused, because the rationale is
what the experiment actually reads and a row without one is unusable rather
than merely incomplete.
"""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CompanyRepo, DbSession
from app.api.routes.auth import CurrentUser
from app.models.account import User
from app.ingestion.prices import get_price_source
from app.models.company import Company
from app.models.experiment import ACTIONS, COHORTS, AgentDecision

logger = logging.getLogger(__name__)
router = APIRouter(tags=["experiment"], prefix="/experiment")

# Enough that a grader has something to read. A one-line rationale is not a
# thought process and the whole trial rests on being able to assess one.
MIN_RATIONALE = 40


class DecisionIn(BaseModel):
    run_id: str = Field(max_length=64)
    cohort: str
    used_loom: bool
    ticker: str = Field(max_length=12)
    action: str
    rationale: str = Field(min_length=MIN_RATIONALE, max_length=8000)
    agent_label: str | None = Field(default=None, max_length=120)
    weight: float | None = Field(default=None, ge=0, le=1)
    conviction: float | None = Field(default=None, ge=0, le=1)
    would_change_mind: str | None = Field(default=None, max_length=2000)
    # Which case-file points the agent actually consulted. Empty is allowed and
    # meaningful: an agent with Loom that used none of it is a real result.
    loom_points_used: list[str] = Field(default_factory=list)


class DecisionOut(BaseModel):
    id: str
    run_id: str
    cohort: str
    used_loom: bool
    ticker: str
    action: str
    weight: float | None
    conviction: float | None
    rationale: str
    would_change_mind: str | None
    loom_points_used: list[str]
    decided_at: datetime
    price_at_decision: float | None
    abnormal_return_pct: float | None


class CohortSummary(BaseModel):
    cohort: str
    used_loom: bool
    decisions: int
    passes: int
    # How often an agent that had Loom actually cited it. The difference
    # between "had the tool" and "used the tool", which is the difference
    # between a correlation and an attribution.
    cited_loom: int
    mean_conviction: float | None
    mean_abnormal_return: float | None
    scored: int


@router.post("/decisions", response_model=DecisionOut, status_code=201)
def submit_decision(
    payload: DecisionIn,
    company_repo: CompanyRepo,
    db: DbSession,
    user: User = CurrentUser,
):
    if payload.cohort not in COHORTS:
        raise HTTPException(status_code=400, detail=f"cohort must be one of {list(COHORTS)}")
    if payload.action not in ACTIONS:
        raise HTTPException(status_code=400, detail=f"action must be one of {list(ACTIONS)}")

    company = company_repo.get_by_ticker(payload.ticker.upper())
    if company is None:
        raise HTTPException(status_code=404, detail=f"Loom does not track {payload.ticker.upper()}.")

    if payload.loom_points_used and not payload.used_loom:
        # Refused rather than silently accepted: a control-arm row citing Loom
        # would quietly contaminate the comparison the whole trial rests on.
        raise HTTPException(
            status_code=400,
            detail="A decision that cites Loom points cannot be in the control arm.",
        )

    # Stamped now, from the live quote, because a decision scored later has to
    # be measured from what it could actually have been executed at.
    price = None
    try:
        series = get_price_source().get(company.ticker, "24H")
        price = series.last if series else None
    except Exception:
        logger.warning("No price for %s at decision time.", company.ticker)

    decision = AgentDecision(
        run_id=payload.run_id,
        cohort=payload.cohort,
        used_loom=payload.used_loom,
        agent_label=payload.agent_label,
        company_id=company.id,
        action=payload.action,
        weight=payload.weight,
        conviction=payload.conviction,
        rationale=payload.rationale.strip(),
        would_change_mind=payload.would_change_mind,
        loom_points_used=payload.loom_points_used,
        loom_snapshot=_snapshot(db, company) if payload.used_loom else {},
        decided_at=datetime.now(timezone.utc),
        price_at_decision=price,
    )
    db.add(decision)
    db.commit()
    db.refresh(decision)
    return _to_out(decision, company.ticker)


@router.get("/decisions", response_model=list[DecisionOut])
def list_decisions(db: DbSession, run_id: str | None = None, limit: int = Query(500, le=2000)):
    query = (
        select(AgentDecision, Company.ticker)
        .join(Company, Company.id == AgentDecision.company_id)
        .order_by(AgentDecision.decided_at.desc())
        .limit(limit)
    )
    if run_id:
        query = query.where(AgentDecision.run_id == run_id)
    return [_to_out(d, ticker) for d, ticker in db.execute(query).all()]


@router.get("/summary", response_model=list[CohortSummary])
def summary(db: DbSession, run_id: str | None = None):
    """The six arms side by side.

    Returns are reported and are not the point: six agents cannot produce a
    return result that clears any bar. The columns that matter are how often
    each arm declined to trade, and how often the Loom arms actually cited it.
    """
    query = select(AgentDecision)
    if run_id:
        query = query.where(AgentDecision.run_id == run_id)
    decisions = list(db.execute(query).scalars())

    grouped: dict[tuple[str, bool], list[AgentDecision]] = {}
    for decision in decisions:
        grouped.setdefault((decision.cohort, decision.used_loom), []).append(decision)

    out: list[CohortSummary] = []
    for (cohort, used_loom), rows in sorted(grouped.items()):
        convictions = [r.conviction for r in rows if r.conviction is not None]
        scored = [r.abnormal_return_pct for r in rows if r.abnormal_return_pct is not None]
        out.append(CohortSummary(
            cohort=cohort,
            used_loom=used_loom,
            decisions=len(rows),
            passes=sum(1 for r in rows if r.action == "pass"),
            cited_loom=sum(1 for r in rows if r.loom_points_used),
            mean_conviction=round(sum(convictions) / len(convictions), 3) if convictions else None,
            mean_abnormal_return=round(sum(scored) / len(scored), 3) if scored else None,
            scored=len(scored),
        ))
    return out


def _snapshot(db, company: Company) -> dict:
    """Loom as it stood when the decision was made.

    Stored on the row because scoring a decision later against evidence Loom
    has since revised would measure the revision rather than the decision.
    """
    from app.models.brief import CompanyBrief
    from app.models.factor import FactorScore

    brief = db.execute(
        select(CompanyBrief)
        .where(CompanyBrief.company_id == company.id)
        .order_by(CompanyBrief.generated_at.desc())
        .limit(1)
    ).scalars().first()

    as_of = db.execute(select(func.max(FactorScore.as_of_date))).scalar()
    factors = {}
    if as_of is not None:
        for score in db.execute(
            select(FactorScore)
            .where(FactorScore.company_id == company.id)
            .where(FactorScore.as_of_date == as_of)
        ).scalars():
            if score.percentile is not None:
                factors[score.factor_key] = round(score.percentile, 4)

    return {
        "stance": brief.stance.value if brief else None,
        "headline": brief.headline if brief else None,
        "confidence": brief.confidence if brief else None,
        "factors": factors,
        "as_of": str(as_of) if as_of else None,
    }


def _to_out(decision: AgentDecision, ticker: str) -> DecisionOut:
    return DecisionOut(
        id=str(decision.id), run_id=decision.run_id, cohort=decision.cohort,
        used_loom=decision.used_loom, ticker=ticker, action=decision.action,
        weight=decision.weight, conviction=decision.conviction,
        rationale=decision.rationale, would_change_mind=decision.would_change_mind,
        loom_points_used=decision.loom_points_used or [],
        decided_at=decision.decided_at, price_at_decision=decision.price_at_decision,
        abnormal_return_pct=decision.abnormal_return_pct,
    )
