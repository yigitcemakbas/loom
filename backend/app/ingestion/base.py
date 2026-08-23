"""Adapter interfaces and DTOs every ingestion source conforms to.

Two interfaces, split by output shape (SRP: an adapter has exactly one
job — produce documents, or produce facts, never both). `DocumentSourceAdapter`
is what Phase 1 needs (SEC EDGAR); `FactSourceAdapter` is here now so the
contract is fixed early, even though its first concrete adapter doesn't
land until Phase 5 (see docs/plan.md).

Adding a new source later — of either shape — means writing one class that
implements one of these interfaces and registering it in registry.py. No
other code needs to change.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class RawDocumentDTO:
    """What every DocumentSourceAdapter hands back — one per ingested document.

    Deliberately plain data, no ORM/session/blob-store references: an
    adapter's only job is "go get the content," never "know how it's
    stored." The orchestrator (registry.ingest_all) is what writes this
    into BlobStore + DocumentRepository.
    """

    company_ticker: str
    source_type: str  # matches app.models.document.SourceType values
    source_name: str
    raw_text: str
    source_url: str | None = None
    doc_subtype: str | None = None
    title: str | None = None
    published_at: datetime | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class StructuredFactDTO:
    """What every FactSourceAdapter hands back (Phase 5+) — one per fact
    (an insider transaction, a short-interest reading, a patent filing, ...).
    """

    company_ticker: str
    fact_type: str  # matches the fact_type enum values, e.g. 'insider_transaction'
    source_name: str
    as_of_date: datetime
    value: float | None = None
    unit: str | None = None
    source_url: str | None = None
    attributes: dict = field(default_factory=dict)


class DocumentSourceAdapter(ABC):
    """One instance per data source that produces text documents."""

    source_name: str
    source_type: str

    @abstractmethod
    def fetch(self, ticker: str, since: datetime | None = None) -> list[RawDocumentDTO]:
        """Fetch new documents for a ticker, optionally only those since a timestamp."""
        ...


class FactSourceAdapter(ABC):
    """One instance per data source that produces structured numeric/tabular facts."""

    source_name: str
    source_type: str

    @abstractmethod
    def fetch(self, ticker: str, since: datetime | None = None) -> list[StructuredFactDTO]:
        """Fetch new facts for a ticker, optionally only those since a timestamp."""
        ...
