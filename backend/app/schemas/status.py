import uuid
from datetime import datetime

from pydantic import BaseModel

from app.models.signal import AnalysisStatus


class AnalysisRunOut(BaseModel):
    id: uuid.UUID
    ticker: str
    doc_subtype: str | None
    prompt_version: str
    status: AnalysisStatus
    error: str | None
    signal_count: int
    created_at: datetime


class UsageRunOut(BaseModel):
    id: uuid.UUID
    ticker: str
    provider: str
    model: str
    calls: int
    input_tokens: int
    output_tokens: int
    cost_usd: float
    documents_analyzed: int
    created_at: datetime


class SystemStatusResponse(BaseModel):
    analysis_runs: list[AnalysisRunOut]
    total_runs: int
    failed_runs: int
    usage_runs: list[UsageRunOut]
    total_cost_usd: float
    total_calls: int
