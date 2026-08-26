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
    what_changed: str | None
    source_types: list[str]
    source_labels: list[str]
    signal_count: int
    evidence: dict
    generated_at: datetime
