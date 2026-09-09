"""The boundary between the ORM and the statistical engine.

The math layer must never depend on SQLAlchemy models or database sessions.
It requires plain data structures so that the statistical reasoning remains
pure, deterministic, and independently testable.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


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

    # 0.5 (minor), 1.0 (moderate), or 2.0 (major).
    magnitude: float

    # A continuous score between -1.0 and 1.0. Null if the finding type
    # does not carry a sentiment judgement.
    sentiment: Optional[float] = None