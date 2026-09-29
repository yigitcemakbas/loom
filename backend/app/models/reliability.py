"""What each kind of finding has actually been worth, measured against outcomes.

`engine/priority.py` ranks findings by a hand-written table of type weights. Those
weights encode a real and defensible epistemology — a deterministic two-filing
comparison outranks an unverifiable language judgement — but they are an opinion,
written once, and nothing has ever checked them against what the findings went on
to be worth. `engine/evaluation.py` has been able to answer that question for
most of the project's life and gated nothing.

This table is the record that closes it: per signal type, the realised hit rate
against a no-skill baseline, and the multiplier that follows.

**It is designed to stay inert until it has earned the right not to be.** A
multiplier is 1.0 — the hand-written weight, unchanged — unless the type clears a
sample floor and a t-statistic bar. With 695 findings across eight types, every
one of them is below the floor today, and that is the correct behaviour rather
than a limitation: fitting eight weights to a few dozen noisy observations each is
the mistake `quant/relevance.py` refuses by name.
"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class SignalReliability(Base):
    """One signal type's measured track record at one horizon."""

    __tablename__ = "signal_reliability"

    signal_type: Mapped[str] = mapped_column(String(64), primary_key=True)
    horizon_sessions: Mapped[int] = mapped_column(Integer, primary_key=True)

    observations: Mapped[int] = mapped_column(Integer, nullable=False)
    hit_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    # What a caller with no skill scores by always predicting whichever direction
    # dominated the period. A hit rate below this is worse than useless, and one
    # just above it is not an edge.
    baseline_hit_rate: Mapped[float | None] = mapped_column(Float, nullable=True)
    spread: Mapped[float | None] = mapped_column(Float, nullable=True)
    t_statistic: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Whether this row is allowed to affect ranking at all.
    trusted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    multiplier: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
