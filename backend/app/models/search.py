import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Index, Integer, func
from sqlalchemy.dialects.postgresql import TSVECTOR, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class DocumentSearchIndex(Base):
    """Postgres full-text index over document content, one row per document.

    A separate table rather than a column on `raw_documents`, for the same
    reason that table has no text column at all (see docs/plan.md "Storage
    Design"): document metadata is read constantly, by the timeline, the
    dashboard, and every signal card, and none of those reads want a tsvector
    of a 400KB filing dragged along with them.

    Deliberately stores the vector and not the text. Content still lives in the
    BlobStore and is read back only for the handful of results actually being
    shown, so search adds an index, not a second copy of every filing.
    """

    __tablename__ = "document_search_index"
    __table_args__ = (
        # Declared here as well as in the migration so `alembic check` can see
        # that model and database agree. Without the GIN index every search
        # degrades to a sequential scan, which is the one thing the tsvector
        # column exists to prevent.
        Index(
            "ix_document_search_index_vector",
            "search_vector",
            postgresql_using="gin",
        ),
    )

    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("raw_documents.id", ondelete="CASCADE"),
        primary_key=True,
    )
    # Weighted: title terms rank above body terms, so searching a company's
    # own name surfaces filings titled for it rather than every filing that
    # mentions it in passing.
    search_vector: Mapped[str] = mapped_column(TSVECTOR, nullable=False)
    content_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    indexed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
