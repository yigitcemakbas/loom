from datetime import date

from pydantic import BaseModel


class EarningsOutlookOut(BaseModel):
    """The read for a company's next earnings event.

    `reports_seen` is 0 on a free Finnhub key, which returns scheduled dates and
    consensus but no historical actuals. Clients should hide the track record
    rather than render an empty one.
    """

    ticker: str
    next_date: date | None
    days_until: int | None
    when_label: str | None
    eps_estimate: float | None
    revenue_estimate: float | None
    quarter_label: str | None
    is_imminent: bool
    reports_seen: int
    beats: int
    misses: int
    average_surprise_percent: float | None
    last_surprise_percent: float | None
    headline: str
