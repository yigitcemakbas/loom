import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SignalType(str, enum.Enum):
    SENTIMENT_SHIFT = "sentiment_shift"
    NEW_RISK_FACTOR = "new_risk_factor"
    NOTABLE_QUOTE = "notable_quote"
    QOQ_ANOMALY = "qoq_anomaly"
    GUIDANCE_CHANGE = "guidance_change"
    # Synthesised across several disclosures inside a short window rather than
    # extracted from any one of them (see engine/clustering.py).
    EMERGING_PATTERN = "emerging_pattern"
    # Derived from structured_facts by arithmetic thresholds, with no model
    # call at all (see engine/fact_rules.py). Both declared together so the
    # enum is not migrated twice, though FINRA short interest lands later.
    INSIDER_ACTIVITY = "insider_activity"
    SHORT_INTEREST_SPIKE = "short_interest_spike"


class Signal(Base):
    """One actionable finding derived from a document.

    Every signal must be traceable to the exact text that produced it:
    `evidence_quote` holds a verbatim excerpt and `source_document_id` points
    at the filing it came from. Nothing is shown in the dashboard without
    that receipt.
    """

    __tablename__ = "signals"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    signal_type: Mapped[SignalType] = mapped_column(SAEnum(SignalType, name="signal_type"), nullable=False)

    summary: Mapped[str] = mapped_column(Text, nullable=False)      # one-line "why this matters"
    # The market-reaction rationale sentence lives here, e.g. "Guidance cuts
    # of this size typically prompt an immediate negative reaction as the
    # market re-rates near-term earnings expectations." Qualitative and
    # grounded in the finding, never a specific price or percentage target.
    detail: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Directional market-reaction characterization, paired with `detail`
    # above. Deliberately three qualitative fields, not a number: nothing in
    # this pipeline has a pricing model, so a specific price/percentage would
    # be fabricated precision. "positive" | "negative" | "neutral";
    # "minor" | "moderate" | "major"; "near_term" | "multi_quarter" | "structural".
    market_direction: Mapped[str | None] = mapped_column(String, nullable=True)
    market_magnitude: Mapped[str | None] = mapped_column(String, nullable=True)
    market_horizon: Mapped[str | None] = mapped_column(String, nullable=True)

    sentiment_score: Mapped[float | None] = mapped_column(Float, nullable=True)  # -1.0 .. 1.0
    confidence: Mapped[float] = mapped_column(Float, nullable=False)             # 0.0 .. 1.0
    priority: Mapped[float] = mapped_column(Float, nullable=False, default=0.0, index=True)

    evidence_quote: Mapped[str | None] = mapped_column(Text, nullable=True)

    source_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_documents.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # Populated only for comparison signals (e.g. this year's 10-K vs last year's).
    compared_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_documents.id", ondelete="SET NULL"), nullable=True
    )

    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    # Raw LLM response, prompt version, and model id, kept so a signal can be
    # audited or reprocessed without re-calling the API.
    signal_metadata: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Null means "not yet reviewed" / "not dismissed", the feed's default
    # state. reviewed_at is set the moment a user writes a note (see
    # SignalRepository.set_note), reviewing a finding means actually
    # forming a judgment on it, not clicking an empty acknowledgment.
    # dismissed_at is a separate, lighter action: hiding noise.
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    dismissed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


class AnalysisStatus(str, enum.Enum):
    COMPLETED = "completed"
    FAILED = "failed"


class DocumentAnalysis(Base):
    """Record that a document was analysed at a given prompt version.

    This is what makes re-running the engine idempotent: the unique constraint
    turns "has this already been analysed?" into an indexed lookup rather than
    a scan of signal metadata. Bumping `prompt_version` is the deliberate way
    to reprocess after improving a prompt.
    """

    __tablename__ = "document_analyses"
    __table_args__ = (
        UniqueConstraint("document_id", "prompt_version", name="uq_document_analyses_doc_version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_documents.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prompt_version: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[AnalysisStatus] = mapped_column(SAEnum(AnalysisStatus, name="analysis_status"), nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    signal_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
