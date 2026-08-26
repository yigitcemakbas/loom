import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SourceType(str, enum.Enum):
    SEC_EDGAR_FILING = "sec_edgar_filing"
    NEWS_API = "news_api"
    SCRAPED_TRANSCRIPT = "scraped_transcript"
    SCRAPED_EARNINGS_REPORT = "scraped_earnings_report"


class RawDocument(Base):
    """Unified metadata row for every ingested document, regardless of source.

    The actual content lives in the BlobStore (see app/storage/blob_store.py)
    and is referenced here only by `blob_uri`, this table intentionally does
    not carry a giant text column. See docs/plan.md "Storage Design".
    """

    __tablename__ = "raw_documents"
    __table_args__ = (
        UniqueConstraint("company_id", "content_hash", name="uq_raw_documents_company_hash"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False, index=True
    )
    source_type: Mapped[SourceType] = mapped_column(SAEnum(SourceType, name="source_type"), nullable=False)
    source_name: Mapped[str] = mapped_column(String, nullable=False)  # e.g. 'sec-edgar', 'fool.com'
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)
    doc_subtype: Mapped[str | None] = mapped_column(String, nullable=True)  # '10-K', '8-K', 'earnings_call', ...
    title: Mapped[str | None] = mapped_column(String, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    blob_uri: Mapped[str] = mapped_column(String, nullable=False)
    doc_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
