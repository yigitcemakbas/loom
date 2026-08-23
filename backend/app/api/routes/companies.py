"""Thin routes: parse request -> call repository -> serialize response.
No business logic lives here (see docs/plan.md "Architecture Principles").
"""

from fastapi import APIRouter, HTTPException

from app.api.deps import CompanyRepo, DocumentRepo
from app.schemas.company import CompanyOut
from app.schemas.document import RawDocumentOut

router = APIRouter(prefix="/companies", tags=["companies"])


@router.get("", response_model=list[CompanyOut])
def list_companies(company_repo: CompanyRepo, q: str | None = None):
    if q:
        return company_repo.search(q)
    return company_repo.list_all()


@router.get("/{ticker}", response_model=CompanyOut)
def get_company(ticker: str, company_repo: CompanyRepo):
    company = company_repo.get_by_ticker(ticker)
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")
    return company


@router.get("/{ticker}/timeline", response_model=list[RawDocumentOut])
def get_company_timeline(ticker: str, company_repo: CompanyRepo, document_repo: DocumentRepo):
    """Phase 1: raw documents only. Phase 2+ merges in `signals` as well,
    without changing this route's shape from the frontend's perspective.
    """
    company = company_repo.get_by_ticker(ticker)
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")
    return document_repo.list_timeline(company.id)
