from fastapi import APIRouter, HTTPException

from app.api.deps import DocumentRepo
from app.schemas.document import RawDocumentDetail, RawDocumentOut
from app.storage.blob_store import get_blob_store

router = APIRouter(prefix="/documents", tags=["documents"])


@router.get("/{document_id}", response_model=RawDocumentDetail)
def get_document(document_id: str, document_repo: DocumentRepo):
    """Source drill-down: metadata comes from Postgres, content comes from
    the BlobStore — the two storage concerns stay separate all the way
    through to this response.
    """
    doc = document_repo.get_by_id(document_id)
    if doc is None:
        raise HTTPException(status_code=404, detail="Document not found")
    content = get_blob_store().get(doc.blob_uri).decode("utf-8", errors="replace")
    base = RawDocumentOut.model_validate(doc)
    return RawDocumentDetail(**base.model_dump(), content=content)
