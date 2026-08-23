"""Maps source_type -> adapter instance, and orchestrates ingestion.

Adding a new source means: write a `DocumentSourceAdapter` or
`FactSourceAdapter`, add one line to the relevant list below. Nothing
else in the codebase needs to change — this is the concrete payoff of
the adapter pattern described in docs/plan.md.

`ingest_all` is the one place that knows how to route each adapter's
output to storage: RawDocumentDTOs go through BlobStore + DocumentRepository,
StructuredFactDTOs (Phase 5+) go through FactRepository. Individual adapters
never touch either.
"""

import hashlib
import logging
from datetime import datetime

from sqlalchemy.orm import Session

from app.ingestion.base import DocumentSourceAdapter, FactSourceAdapter, RawDocumentDTO
from app.ingestion.sec_edgar import SecEdgarAdapter
from app.models.document import SourceType
from app.repositories.company_repository import CompanyRepository
from app.repositories.document_repository import DocumentRepository
from app.storage.blob_store import BlobStore, get_blob_store

logger = logging.getLogger(__name__)

# Phase 1: SEC EDGAR only. Phase 3 adds a Finnhub news adapter and the
# Motley Fool transcript scraper here; Phase 4 adds the earnings
# press-release scraper.
DOCUMENT_ADAPTERS: list[DocumentSourceAdapter] = [
    SecEdgarAdapter(),
]

# Phase 5+: SEC Form 4, SEC 13F, FINRA short interest. Phase 6+: USPTO
# patents, Google Trends. Empty until then.
FACT_ADAPTERS: list[FactSourceAdapter] = []


def _content_hash(raw_text: str) -> str:
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


def _persist_document(
    dto: RawDocumentDTO,
    *,
    company_id,
    db: Session,
    blob_store: BlobStore,
) -> bool:
    """Returns True if a new document was written, False if it was a dedupe skip."""
    document_repo = DocumentRepository(db)
    content_hash = _content_hash(dto.raw_text)

    if document_repo.exists(company_id, content_hash):
        return False

    blob_uri = blob_store.put(
        key=f"{dto.company_ticker.upper()}/{content_hash}.txt",
        content=dto.raw_text.encode("utf-8"),
    )
    created = document_repo.create(
        company_id=company_id,
        source_type=SourceType(dto.source_type),
        source_name=dto.source_name,
        source_url=dto.source_url,
        doc_subtype=dto.doc_subtype,
        title=dto.title,
        published_at=dto.published_at,
        blob_uri=blob_uri,
        content_hash=content_hash,
        doc_metadata=dto.metadata,
    )
    return created is not None


def ingest_all(ticker: str, db: Session, since: datetime | None = None) -> dict[str, int]:
    """Runs every registered adapter for one ticker. Returns counts of newly
    written documents/facts, keyed by adapter source_name, for CLI/API
    reporting. A failure in one adapter never blocks the others.
    """
    company_repo = CompanyRepository(db)
    company = company_repo.get_by_ticker(ticker)
    if company is None:
        raise ValueError(f"No company found for ticker {ticker!r} — seed it first.")

    blob_store = get_blob_store()
    results: dict[str, int] = {}

    for adapter in DOCUMENT_ADAPTERS:
        try:
            dtos = adapter.fetch(ticker, since=since)
        except Exception:
            logger.exception("Ingestion adapter %s failed for %s", adapter.source_name, ticker)
            results[adapter.source_name] = 0
            continue

        new_count = sum(
            1
            for dto in dtos
            if _persist_document(dto, company_id=company.id, db=db, blob_store=blob_store)
        )
        results[adapter.source_name] = new_count

    # FACT_ADAPTERS wired in the same way from Phase 5 onward, routed through
    # FactRepository instead of DocumentRepository/BlobStore.

    return results
