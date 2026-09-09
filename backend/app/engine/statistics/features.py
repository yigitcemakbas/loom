"""The boundary between the ORM and the statistical engine.

The math layer must never depend on SQLAlchemy models or database sessions.
It requires plain data structures so that the statistical reasoning remains
pure, deterministic, and independently testable.

This module is the one place allowed to read a Signal and hand back a number,
which is why the magnitude vocabulary lives here rather than being restated by
every caller that needs it.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional

# The project's single definition of what a magnitude label is worth. It was
# previously declared privately in engine/brief.py and restated inline in
# engine/pipeline.py; brief.py now imports it from here so the two cannot drift.
MAGNITUDE_WEIGHT: dict[str, float] = {"minor": 0.5, "moderate": 1.0, "major": 2.0}

# The order severity runs in, for reporting and for rate tables that should
# read the same way every time.
MAGNITUDE_LABELS: tuple[str, ...] = ("minor", "moderate", "major")


def magnitude_label_of(signal) -> Optional[str]:
    """The magnitude label of a signal, or None when it was never assessed.

    Deliberately duck-typed on `market_magnitude` rather than importing the ORM
    model, so this module keeps its promise not to depend on SQLAlchemy.
    """
    label = getattr(signal, "market_magnitude", None)
    if label in MAGNITUDE_WEIGHT:
        return label
    return None


def magnitude_of(signal) -> Optional[float]:
    """The numeric weight of a signal's magnitude, or None when unassessed.

    **Returning None for an unassessed signal is the whole point of this
    function.** The previous inline expression ended in `else 1.0`, so a signal
    whose magnitude was never assessed was silently scored "moderate". Just
    under half of the stored corpus is unassessed, so that fallback fed a large
    block of identical synthetic values into every baseline, which shrinks the
    spread and makes ordinary findings look like anomalies.

    A caller that genuinely wants a default can apply one itself; what it must
    not do is receive one without knowing. `engine/brief.py` does exactly that
    on purpose, treating unassessed as moderate when folding a stance, because
    there every finding has to carry some weight. A baseline is the opposite
    case: a value that was never measured has to be absent, not invented.
    """
    label = magnitude_label_of(signal)
    return MAGNITUDE_WEIGHT[label] if label is not None else None


@dataclass
class EvidenceFeature:
    """A numerical representation of a single qualitative finding.

    Loom's extraction layer produces categorical tags (e.g., magnitude="major").
    This structure translates those string categories into floats so the
    engine can reason about them arithmetically.
    """

    # Kept for traceability so a statistical conclusion can point exactly
    # to the finding that produced it.
    signal_id: str

    signal_type: str

    # -1.0 (negative), 0.0 (neutral), or 1.0 (positive).
    direction: float

    # The LLM's reported certainty in the extraction, bounded 0.0 to 1.0.
    confidence: float

    occurred_at: datetime

    # 0.5 (minor), 1.0 (moderate), 2.0 (major), or None when the finding was
    # never assessed for market impact.
    magnitude: Optional[float] = None

    # The label behind `magnitude`. The categorical form is what the rate
    # model actually reasons over; the float is kept for weighting.
    magnitude_label: Optional[str] = None

    # A continuous score between -1.0 and 1.0. Null if the finding type
    # does not carry a sentiment judgement.
    sentiment: Optional[float] = None


def feature_from_signal(signal) -> EvidenceFeature:
    """Build a feature from an ORM signal.

    Lives here rather than in the pipeline so that the ORM-to-float conversion
    exists once. The pipeline previously rebuilt this inline, which is how the
    magnitude mapping came to disagree with itself in three places.
    """
    direction_label = getattr(signal, "market_direction", None)
    direction = (
        1.0 if direction_label == "positive"
        else -1.0 if direction_label == "negative"
        else 0.0
    )
    return EvidenceFeature(
        signal_id=str(signal.id),
        signal_type=signal.signal_type.value,
        direction=direction,
        confidence=signal.confidence or 0.0,
        occurred_at=signal.occurred_at,
        magnitude=magnitude_of(signal),
        magnitude_label=magnitude_label_of(signal),
        sentiment=signal.sentiment_score,
    )
