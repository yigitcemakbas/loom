import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.signal import SignalType


class SignalOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    signal_type: SignalType
    summary: str
    detail: str | None
    market_direction: str | None
    market_magnitude: str | None
    market_horizon: str | None
    sentiment_score: float | None
    confidence: float
    priority: float
    evidence_quote: str | None
    source_document_id: uuid.UUID | None
    compared_document_id: uuid.UUID | None
    occurred_at: datetime
    created_at: datetime
    reviewed_at: datetime | None
    dismissed_at: datetime | None
    note: str | None


class SignalWithContext(SignalOut):
    """Feed entries carry their ticker and a link to the source filing so a
    card can show its receipt without the client making extra requests.
    compared_source_url is populated for the comparison signal types, it's the
    document the current one was compared against: the prior-year filing for
    qoq_anomaly, the oldest disclosure in the window for emerging_pattern.

    The two pattern_* fields are derived from signal_metadata rather than
    exposing it wholesale, that column also holds raw model responses, which
    are for auditing, not for the browser.
    """

    ticker: str
    source_url: str | None
    doc_subtype: str | None
    compared_source_url: str | None = None
    pattern_document_count: int | None = None
    pattern_window_days: int | None = None


class SentimentPoint(BaseModel):
    occurred_at: datetime
    sentiment_score: float
    summary: str


class AnalysisTriggerResponse(BaseModel):
    ticker: str
    documents_queued: int
    detail: str


class NoteRequest(BaseModel):
    note: str
