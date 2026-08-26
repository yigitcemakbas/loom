import uuid
from datetime import date, datetime

from pydantic import BaseModel, ConfigDict

from app.models.structured_fact import FactType


class StructuredFactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    company_id: uuid.UUID
    fact_type: FactType
    source_name: str
    source_url: str | None
    as_of_date: date
    value: float | None
    unit: str | None
    attributes: dict
    fetched_at: datetime


class InsiderActivitySummary(BaseModel):
    """Rollup for the company activity panel.

    Open-market counts are reported separately from the totals because they are
    the only ones that reflect a decision to trade; the rest is vesting and
    option mechanics (see engine/fact_rules.py).
    """

    transactions: int
    open_market_transactions: int
    open_market_sold_usd: float
    open_market_bought_usd: float
    distinct_insiders: int
    latest_transaction_date: date | None
