import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.brief import Stance


class DriverOut(BaseModel):
    title: str
    detail: str
    direction: str
    magnitude: str
    sources: list[str] = []
    signal_ids: list[str] = []
    # How often this company produces findings of this severity, and how many
    # prior findings that rests on. Null where no baseline could be formed,
    # which the client must render differently from "ordinary".
    evidence_rate: float | None = None
    evidence_sample_size: int | None = None
    # How old this evidence actually is, counting the period the quote describes
    # rather than the date of the document that carried it, and a note where
    # those two differ enough to change how it should be read. Both default to
    # None so briefs stored before this existed deserialise unchanged.
    age_days: int | None = None
    stale_note: str | None = None


class BriefOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    stance: Stance
    # Plain-language rendering of the stance, resolved server-side so every
    # client shows the same words for the same verdict.
    stance_label: str
    headline: str
    confidence: float
    drivers: list[DriverOut]
    counterpoint: DriverOut | None = None
    what_changed: str | None
    source_types: list[str]
    source_labels: list[str]
    signal_count: int
    evidence: dict
    generated_at: datetime
