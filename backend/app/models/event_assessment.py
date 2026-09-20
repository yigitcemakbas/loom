import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class EventAssessment(Base):
    """What Loom made of a live event, and when it knew.

    Stored rather than merely logged for two reasons that both matter more than
    display. The first is auditability: an assessment is only meaningful
    alongside the prior that produced it, and priors change, so the verdict has
    to be pinned to the beliefs that were actually held at the time.

    The second is the point of the whole exercise. Every row here records a
    claim with a timestamp, against a company whose price history is already
    available, which means the question the project has never been able to
    answer (has any of this ever been right) becomes a join rather than a
    research project. Nothing measures that yet. This table is what makes it
    possible to.

    `latency_seconds` is kept because it is the number this architecture exists
    to reduce, and an optimisation nobody measures is a story rather than a
    result.
    """

    __tablename__ = "event_assessments"
    __table_args__ = (
        # One assessment per filing. The watcher polls on an interval shorter
        # than the feed's retention window, so the same filing is seen several
        # times by design; the constraint is what makes re-seeing it free.
        UniqueConstraint("company_id", "external_id", name="uq_event_assessment_external"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    prior_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("company_priors.id", ondelete="SET NULL"), nullable=True
    )

    # Accession number for a filing; whatever identifies the event otherwise.
    external_id: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False)       # filing | earnings | news
    form: Mapped[str | None] = mapped_column(String, nullable=True)
    source_url: Mapped[str | None] = mapped_column(String, nullable=True)

    score: Mapped[float] = mapped_column(Float, nullable=False)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    headline: Mapped[str] = mapped_column(Text, nullable=False)

    # [{topic, direction, severity, matched_keywords, already_priced}]
    matches: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    surprises: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    amplifiers: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # When the market learned, versus when Loom did.
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
    latency_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    scoring_ms: Mapped[float | None] = mapped_column(Float, nullable=True)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<EventAssessment {self.external_id} {self.direction} {self.score}>"
