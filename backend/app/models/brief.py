import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class Stance(str, enum.Enum):
    """The single answer a brief exists to give.

    Deliberately coarse. A reader deciding whether to look closer needs to know
    which way the evidence leans and how strongly, not a spurious 1-100 score
    that implies precision the underlying material cannot support.
    """

    STRONG_NEGATIVE = "strong_negative"
    NEGATIVE = "negative"
    MIXED = "mixed"
    POSITIVE = "positive"
    STRONG_POSITIVE = "strong_positive"
    QUIET = "quiet"            # coverage exists, nothing directional in it
    INSUFFICIENT = "insufficient"  # not enough analysed material to say anything


class CompanyBrief(Base):
    """One company's current read, folded from every signal and fact about it.

    This is the product's actual deliverable. Everything else in the database,
    filings, transcripts, news, insider trades, extracted findings, exists to
    produce this row: one stance, one sentence, and the handful of things
    driving it. A reader who sees only this should be able to decide whether
    the company needs their attention today.

    Rows are kept rather than overwritten so "what changed" is a real diff
    against the previous read instead of a claim nobody can check.
    """

    __tablename__ = "company_briefs"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )

    stance: Mapped[Stance] = mapped_column(SAEnum(Stance, name="stance"), nullable=False)
    # Plain-language one-liner. No jargon, no ticker-speak: this is the line a
    # reader sees before deciding whether to read anything else.
    headline: Mapped[str] = mapped_column(Text, nullable=False)
    # How much the evidence supports the stance, 0..1. Driven mostly by whether
    # independent source types agree, not by how many findings there are.
    confidence: Mapped[float] = mapped_column(Float, nullable=False)

    # [{title, detail, direction, magnitude, sources: [...], signal_ids: [...]}]
    drivers: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    # The strongest finding arguing against the stance, same shape as a driver.
    # Stored rather than derived on read so a brief remains a complete record of
    # what was concluded and what was weighed against it.
    counterpoint: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # What is new since the previous brief, so a returning reader can skip
    # everything they have already seen.
    what_changed: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Which source types backed this read, e.g. ["10-K", "earnings_call", "news"].
    # Agreement across independent kinds of source is the real confidence input.
    source_types: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    signal_count: Mapped[int] = mapped_column(default=0)
    evidence: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    generated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    # Bumped when the synthesis logic changes, the same way PROMPT_VERSION
    # works for document analysis.
    engine_version: Mapped[str] = mapped_column(String, nullable=False)
