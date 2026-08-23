import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.schemas.company import CompanyOut


class WatchlistOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    owner_email: str | None
    created_at: datetime


class WatchlistCreate(BaseModel):
    name: str = "Default"
    owner_email: str | None = None


class WatchlistWithCompanies(WatchlistOut):
    companies: list[CompanyOut] = []


class AddTickerRequest(BaseModel):
    ticker: str
