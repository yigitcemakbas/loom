import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict


class CompanyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    ticker: str
    name: str
    cik: str | None
    sector: str | None
    exchange: str | None
    created_at: datetime


class CompanyCreate(BaseModel):
    ticker: str
    name: str
    cik: str | None = None
    sector: str | None = None
    exchange: str | None = None
