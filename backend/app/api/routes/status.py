"""System status: analysis job history + LLM usage/cost.

Both tables (document_analyses, llm_usage_runs) are already populated by
every analysis run, this route is purely the read path that was missing.
"""

from fastapi import APIRouter
from sqlalchemy import func, select

from app.api.deps import CompanyRepo, DbSession, DocumentRepo, SignalRepo, UsageRepo
from app.models.company import Company
from app.models.document import RawDocument
from app.models.signal import AnalysisStatus, Signal
from app.schemas.status import AnalysisRunOut, SystemStatusResponse, UsageRunOut

router = APIRouter(tags=["status"])


@router.get("/status", response_model=SystemStatusResponse)
def get_status(
    signal_repo: SignalRepo,
    usage_repo: UsageRepo,
    company_repo: CompanyRepo,
    document_repo: DocumentRepo,
):
    runs = signal_repo.list_analysis_runs(limit=200)
    run_rows: list[AnalysisRunOut] = []
    for run in runs:
        document = document_repo.get_by_id(run.document_id)
        company = company_repo.get_by_id(document.company_id) if document else None
        run_rows.append(
            AnalysisRunOut(
                id=run.id,
                ticker=company.ticker if company else "?",
                doc_subtype=document.doc_subtype if document else None,
                prompt_version=run.prompt_version,
                status=run.status,
                error=run.error,
                signal_count=run.signal_count,
                created_at=run.created_at,
            )
        )

    usage_runs = usage_repo.list_recent(limit=200)
    usage_rows = [UsageRunOut(**{f: getattr(u, f) for f in UsageRunOut.model_fields}) for u in usage_runs]

    return SystemStatusResponse(
        analysis_runs=run_rows,
        total_runs=len(run_rows),
        failed_runs=sum(1 for r in run_rows if r.status == AnalysisStatus.FAILED),
        usage_runs=usage_rows,
        total_cost_usd=sum(u.cost_usd for u in usage_rows),
        total_calls=sum(u.calls for u in usage_rows),
    )


@router.get("/coverage-stats")
def coverage_stats(db: DbSession) -> dict:
    """Counts describing the size of what this instance holds.

    Open, like /status and /health, and deliberately aggregate-only: it answers
    "how much has Loom read" without naming a company, a finding or a filing.
    Nothing here is derivable about any individual issuer.

    It exists for the sign-in screen. That screen used to carry three sentences
    of marketing and now reports what the instance can actually account for,
    which needed numbers that an unauthenticated visitor is allowed to see.
    """
    def count(model, *where):
        q = select(func.count()).select_from(model)
        for clause in where:
            q = q.where(clause)
        return int(db.execute(q).scalar() or 0)

    periodic = ("10-K", "10-Q", "8-K")
    return {
        "companies_tracked": count(Company),
        "companies_read": int(db.execute(
            select(func.count(func.distinct(Signal.company_id)))
            .where(Signal.dismissed_at.is_(None))
        ).scalar() or 0),
        "documents": count(RawDocument),
        "sec_filings": count(RawDocument, RawDocument.doc_subtype.in_(periodic)),
        "transcripts": count(RawDocument, RawDocument.doc_subtype == "earnings_call"),
        "news_items": count(RawDocument, RawDocument.doc_subtype == "news"),
        "findings": count(Signal, Signal.dismissed_at.is_(None)),
        # The newest thing Loom has read, so the page shows currency and not
        # just volume. A large corpus that stopped updating is a different
        # claim from a large corpus that is current.
        "latest_document": (lambda d: d.isoformat() if d else None)(
            db.execute(select(func.max(RawDocument.published_at))).scalar()
        ),
    }
