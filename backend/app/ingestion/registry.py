"""Maps source_type -> adapter instance, and orchestrates ingestion.

Adding a new source means: write a `DocumentSourceAdapter` or
`FactSourceAdapter`, add one line to the relevant list below. Nothing
else in the codebase needs to change, this is the concrete payoff of
the adapter pattern described in docs/plan.md.

`ingest_all` is the one place that knows how to route each adapter's
output to storage: RawDocumentDTOs go through BlobStore + DocumentRepository,
StructuredFactDTOs (Phase 5+) go through FactRepository. Individual adapters
never touch either.
"""

import hashlib
import logging
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

import json

from app.ingestion.base import (
    DocumentSourceAdapter,
    FactSourceAdapter,
    RawDocumentDTO,
    StructuredFactDTO,
)
from app.ingestion.facts.earnings_calendar import EarningsCalendarAdapter
from app.ingestion.facts.sec_form4 import SecForm4Adapter
from app.ingestion.news_api import FinnhubNewsAdapter
from app.ingestion.scrapers.earnings_transcript_motley_fool import MotleyFoolTranscriptScraper
from app.ingestion.sec_edgar import SecEdgarAdapter
from app.models.document import SourceType
from app.models.structured_fact import FactType
from app.repositories.company_repository import CompanyRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.fact_repository import FactRepository
from app.repositories.search_repository import SearchRepository
from app.storage.blob_store import BlobStore, get_blob_store

logger = logging.getLogger(__name__)

# These are parallel, first-class sources, not fallbacks for one another:
# a run where Finnhub has no key still ingests filings and transcripts, and
# `ingest_all` isolates each adapter's failures from the rest. Phase 4 adds the
# earnings press-release scraper to this same list.
DOCUMENT_ADAPTERS: list[DocumentSourceAdapter] = [
    SecEdgarAdapter(),
    FinnhubNewsAdapter(),
    MotleyFoolTranscriptScraper(),
]

# Sources whose output is numbers rather than prose. Phase 5 continues with
# SEC 13F and FINRA short interest; Phase 6 adds USPTO patents and Google
# Trends. They route to FactRepository instead of BlobStore + DocumentRepository.
FACT_ADAPTERS: list[FactSourceAdapter] = [
    SecForm4Adapter(),
    EarningsCalendarAdapter(),
]


def _content_hash(raw_text: str) -> str:
    return hashlib.sha256(raw_text.encode("utf-8")).hexdigest()


def _fact_hash(dto: StructuredFactDTO) -> str:
    """Stable identity for one fact.

    Computed generically from the DTO rather than per adapter, so a new fact
    source gets dedupe for free instead of each one inventing its own key and
    getting it subtly wrong. `attributes` is included and key-sorted because it
    is what distinguishes two otherwise identical rows, for example two officers
    reporting the same size trade on the same day.
    """
    payload = json.dumps(
        {
            "fact_type": dto.fact_type,
            "source_name": dto.source_name,
            "as_of_date": dto.as_of_date.date().isoformat(),
            "value": dto.value,
            "unit": dto.unit,
            "attributes": dto.attributes,
        },
        sort_keys=True,
        default=str,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _persist_fact(dto: StructuredFactDTO, *, company_id, db: Session) -> bool:
    """Returns True if a new fact row was written, False if it deduped."""
    created = FactRepository(db).create(
        company_id=company_id,
        fact_type=FactType(dto.fact_type),
        source_name=dto.source_name,
        as_of_date=dto.as_of_date.date(),
        value=dto.value,
        unit=dto.unit,
        source_url=dto.source_url,
        attributes=dto.attributes,
        content_hash=_fact_hash(dto),
    )
    return created is not None


def _persist_document(
    dto: RawDocumentDTO,
    *,
    company_id,
    db: Session,
    blob_store: BlobStore,
) -> bool:
    """Returns True if a new document was written, False if it was a dedupe skip.

    Two dedupe keys, checked in this order:

      source_url    the document's stable identity, which survives changes to
                    how its text is extracted (see exists_by_source_url).
      content_hash  the fallback for sources that supply no URL, and the guard
                    against the same content arriving under two URLs.
    """
    document_repo = DocumentRepository(db)
    content_hash = _content_hash(dto.raw_text)

    if dto.source_url and document_repo.exists_by_source_url(company_id, dto.source_url):
        return False
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
    if created is None:
        return False

    # Indexed at ingestion rather than at analysis: a document should be
    # findable as soon as it exists, and most documents are never analysed
    # (the engine deliberately works on a bounded recent scope). Failing to
    # index must not lose the document itself, which is already stored.
    try:
        SearchRepository(db).index_document(
            created.id, title=created.title, content=dto.raw_text
        )
    except Exception:
        logger.exception("Search indexing failed for document %s", created.id)

    return True


# Re-examined on every incremental run, so a document that appears slightly
# out of order, or is published while a run is in flight, is not missed
# forever. Cheap: dedupe discards it in one indexed lookup.
_INCREMENTAL_OVERLAP_DAYS = 7


def ingest_all(ticker: str, db: Session, since: datetime | None = None) -> dict[str, int]:
    """Runs every registered adapter for one ticker. Returns counts of newly
    written documents/facts, keyed by adapter source_name, for CLI/API
    reporting. A failure in one adapter never blocks the others.

    When `since` is not given it is derived from what has already been stored,
    so a repeat run fetches only genuinely new material. This is not merely an
    optimisation: adapters do real work per document before dedupe can see it
    (an 8-K now pulls its exhibit list and exhibits), so without a cutoff every
    refresh would re-download a company's entire filing history just to throw
    all of it away. A company with nothing stored still gets the full backfill.
    """
    company_repo = CompanyRepository(db)
    company = company_repo.get_by_ticker(ticker)
    if company is None:
        raise ValueError(f"No company found for ticker {ticker!r}, seed it first.")

    if since is None:
        newest = DocumentRepository(db).newest_published_at(company.id)
        if newest is not None:
            since = newest - timedelta(days=_INCREMENTAL_OVERLAP_DAYS)
            logger.info("Incremental ingest for %s since %s", ticker, since.date())

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

    for fact_adapter in FACT_ADAPTERS:
        try:
            fact_dtos = fact_adapter.fetch(ticker, since=since)
        except Exception:
            logger.exception("Fact adapter %s failed for %s", fact_adapter.source_name, ticker)
            results[fact_adapter.source_name] = 0
            continue

        results[fact_adapter.source_name] = sum(
            1 for dto in fact_dtos if _persist_fact(dto, company_id=company.id, db=db)
        )

    return results
