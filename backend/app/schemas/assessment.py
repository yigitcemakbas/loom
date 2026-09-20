from datetime import datetime

from pydantic import BaseModel, ConfigDict


class EventAssessmentOut(BaseModel):
    """One live reaction, with the latency it was reached in."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    ticker: str
    kind: str
    form: str | None
    source_url: str | None
    score: float
    direction: str
    headline: str
    matches: list
    surprises: dict
    amplifiers: list
    occurred_at: datetime
    assessed_at: datetime
    latency_seconds: float | None
    scoring_ms: float | None
