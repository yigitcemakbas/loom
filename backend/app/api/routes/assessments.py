"""Live event assessments. Thin route over AssessmentRepository."""

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import AssessmentRepo, CompanyRepo
from app.schemas.assessment import EventAssessmentOut

router = APIRouter(tags=["assessments"])


def _to_out(assessment, ticker: str) -> EventAssessmentOut:
    return EventAssessmentOut(
        id=str(assessment.id),
        ticker=ticker,
        kind=assessment.kind,
        form=assessment.form,
        source_url=assessment.source_url,
        score=assessment.score,
        direction=assessment.direction,
        headline=assessment.headline,
        matches=assessment.matches or [],
        surprises=assessment.surprises or {},
        amplifiers=assessment.amplifiers or [],
        exposed=assessment.exposed or [],
        occurred_at=assessment.occurred_at,
        assessed_at=assessment.assessed_at,
        latency_seconds=assessment.latency_seconds,
        scoring_ms=assessment.scoring_ms,
    )


@router.get("/assessments", response_model=list[EventAssessmentOut])
def list_assessments(
    assessment_repo: AssessmentRepo,
    company_repo: CompanyRepo,
    limit: int = Query(default=50, le=200),
    min_score: float | None = None,
):
    """Recent live reactions, newest event first.

    `min_score` is the filter that matters in practice: most filings match
    nothing a company was being watched for, and that silence is the normal
    case rather than a failure.
    """
    out = []
    for assessment in assessment_repo.recent(limit=limit, min_score=min_score):
        company = company_repo.get_by_id(assessment.company_id)
        out.append(_to_out(assessment, company.ticker if company else "?"))
    return out


@router.get("/companies/{ticker}/assessments", response_model=list[EventAssessmentOut])
def company_assessments(
    ticker: str,
    assessment_repo: AssessmentRepo,
    company_repo: CompanyRepo,
    limit: int = Query(default=50, le=200),
):
    company = company_repo.get_by_ticker(ticker)
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")
    return [
        _to_out(a, company.ticker)
        for a in assessment_repo.for_company(company.id, limit=limit)
    ]
