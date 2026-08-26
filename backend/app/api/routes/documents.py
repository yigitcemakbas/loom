from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CompanyRepo, DocumentRepo, SearchRepo
from app.schemas.document import (
    RawDocumentDetail,
    RawDocumentOut,
    RawDocumentWithContext,
    SearchHitOut,
)
from app.storage.blob_store import get_blob_store

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("", response_model=list[RawDocumentWithContext])
def list_documents(
    document_repo: DocumentRepo,
    company_repo: CompanyRepo,
    ticker: str | None = None,
    doc_subtype: str | None = None,
    limit: int = 200,
):
    """Cross-portfolio filing browser, everything ingested, filterable by
    ticker/type, most recent first."""
    company_id = None
    if ticker:
        company = company_repo.get_by_ticker(ticker)
        if company is None:
            raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")
        company_id = company.id

    documents = document_repo.list_all(company_id=company_id, doc_subtype=doc_subtype, limit=limit)
    out: list[RawDocumentWithContext] = []
    for doc in documents:
        company = company_repo.get_by_id(doc.company_id)
        out.append(
            RawDocumentWithContext(
                **RawDocumentOut.model_validate(doc).model_dump(),
                ticker=company.ticker if company else "?",
            )
        )
    return out


@router.get("/search", response_model=list[SearchHitOut])
def search_documents(
    search_repo: SearchRepo,
    company_repo: CompanyRepo,
    q: str = Query(min_length=2, description="Free text; supports quoted phrases, or, -excluded"),
    ticker: str | None = None,
    doc_subtype: str | None = None,
    limit: int = Query(default=25, le=100),
):
    """Full-text search over ingested document content.

    Declared above `/{document_id}` deliberately: routes match in declaration
    order, so the reverse would make "search" parse as a document id.

    Ranking is done entirely in Postgres against the tsvector; content is read
    back from the BlobStore only for the results actually being returned, which
    is what keeps the database free of a second copy of every filing.
    """
    company_id = None
    if ticker:
        company = company_repo.get_by_ticker(ticker)
        if company is None:
            raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")
        company_id = company.id

    hits = search_repo.search(q, company_id=company_id, doc_subtype=doc_subtype, limit=limit)

    blob_store = get_blob_store()
    out: list[SearchHitOut] = []
    for document, rank in hits:
        company = company_repo.get_by_id(document.company_id)
        snippet = None
        try:
            content = blob_store.get(document.blob_uri).decode("utf-8", errors="replace")
            snippet = search_repo.snippet_for(content, q)
        except Exception:
            # A missing blob costs this result its snippet, not its place in
            # the list: the match itself was established from the index.
            pass
        out.append(
            SearchHitOut(
                **RawDocumentOut.model_validate(document).model_dump(),
                ticker=company.ticker if company else "?",
                rank=rank,
                snippet=snippet,
            )
        )
    return out


@router.get("/{document_id}", response_model=RawDocumentDetail)
def get_document(document_id: str, document_repo: DocumentRepo):
    """Source drill-down: metadata comes from Postgres, content comes from
    the BlobStore, the two storage concerns stay separate all the way
    through to this response.
    """
    doc = document_repo.get_by_id(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    content = get_blob_store().get(doc.blob_uri).decode("utf-8", errors="replace")
    base = RawDocumentOut.model_validate(doc)
    return RawDocumentDetail(**base.model_dump(), content=content)
