from datetime import datetime

from pydantic import BaseModel, ConfigDict


class TapeItemOut(BaseModel):
    """One entry on the dashboard's running feed."""

    model_config = ConfigDict(from_attributes=True)

    kind: str
    ticker: str
    label: str
    headline: str
    detail: str | None
    tone: str
    occurred_at: datetime | None
    href: str | None
    sources: list[str]
