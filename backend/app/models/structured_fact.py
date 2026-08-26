import enum
import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, ForeignKey, Index, Numeric, String, UniqueConstraint, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class FactType(str, enum.Enum):
    # Phase 5, regulatory and institutional.
    INSIDER_TRANSACTION = "insider_transaction"
    INSTITUTIONAL_HOLDING = "institutional_holding"
    SHORT_INTEREST = "short_interest"
    # A scheduled or reported earnings event: the date, what the market
    # expects, and afterwards what was actually delivered.
    EARNINGS_EVENT = "earnings_event"
    # Phase 6, alt-data. Declared now so the enum is not migrated twice.
    PATENT_FILING = "patent_filing"
    SEARCH_TREND_INDEX = "search_trend_index"
    JOB_POSTING_COUNT = "job_posting_count"
    APP_STORE_RANKING = "app_store_ranking"


class StructuredFact(Base):
    """One numeric or tabular fact about a company, from a non-prose source.

    The second physical storage shape in the project, parallel to
    `raw_documents`. "Insider Jane Doe sold 5,000 shares at $142.30 on
    2026-08-20" is a row, not a blob of text; forcing it through the document
    pipeline would mean asking a language model to re-extract numbers that
    arrived already structured, which is both expensive and less reliable than
    simply reading the field.

    Because these facts are already structured, the signals derived from them
    need no model at all: `engine/fact_rules.py` applies arithmetic thresholds
    directly. See docs/plan.md "Storage Design".
    """

    __tablename__ = "structured_facts"
    __table_args__ = (
        # content_hash rather than the values themselves: two insiders can file
        # identical-looking transactions on the same date, and the hash of the
        # source row is what tells them apart.
        UniqueConstraint(
            "company_id", "fact_type", "as_of_date", "content_hash",
            name="uq_structured_facts_dedupe",
        ),
        # Every read is "this company's facts of this type, most recent first".
        Index(
            "idx_structured_facts_company_type_date",
            "company_id", "fact_type", "as_of_date",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    fact_type: Mapped[FactType] = mapped_column(SAEnum(FactType, name="fact_type"), nullable=False)

    source_name: Mapped[str] = mapped_column(String, nullable=False)  # 'sec-edgar-form4', 'finra', ...
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)

    # The date the fact pertains to, not when it was fetched. A Form 4 filed
    # today can report a trade from last week, and the trade date is what any
    # clustering rule must reason about.
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False)

    value: Mapped[float | None] = mapped_column(Numeric, nullable=True)
    unit: Mapped[str | None] = mapped_column(String, nullable=True)  # 'shares', 'usd', 'percent', ...

    # Fact-specific fields that do not deserve columns: insider name and role,
    # 13F filer, transaction code, price per share.
    attributes: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    content_hash: Mapped[str] = mapped_column(String, nullable=False)
