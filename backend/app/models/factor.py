"""Stored quantitative factor scores.

One row is one company's reading on one factor as of one date, alongside where
that reading placed it among every other company Loom could measure the same
day. Both halves are kept: the raw value is what can be audited back to a
filing, and the percentile is what a reader can actually interpret.

Scores are stored rather than computed on request because a percentile is a
statement about the whole universe at a moment. Recomputing it later against a
universe that has since filed more would silently change what a past score
meant, and nothing that claims to be point-in-time can afford that.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

# The reserved key under which the folded score is stored, so the composite
# lives in the same table and the same history as the factors it summarises.
COMPOSITE_KEY = "composite"


class FactorScore(Base):
    __tablename__ = "factor_scores"
    __table_args__ = (
        UniqueConstraint("company_id", "as_of_date", "factor_key", name="uq_factor_scores_point"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The date the universe was scored as of. Only filings on or before this
    # date fed the value, which is what makes a stored row replayable.
    as_of_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)
    factor_key: Mapped[str] = mapped_column(String, nullable=False, index=True)

    value: Mapped[float] = mapped_column(Float, nullable=False)
    # Null when the universe was too small to rank against. Distinct from a
    # percentile of zero, which is a real and very bad reading.
    percentile: Mapped[float | None] = mapped_column(Float, nullable=True)
    universe_size: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # The filed figures the value was computed from, so any score can be taken
    # apart without re-running the engine. For the composite row this holds the
    # factor count, which factors were used, and the health-score result.
    inputs: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
