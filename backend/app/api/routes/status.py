"""System status: analysis job history + LLM usage/cost.

Both tables (document_analyses, llm_usage_runs) are already populated by
every analysis run, this route is purely the read path that was missing.
"""

from app.models.signal import AnalysisStatus
from fastapi import APIRouter

from app.api.deps import CompanyRepo, DocumentRepo, SignalRepo, UsageRepo
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
