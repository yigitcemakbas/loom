from pydantic import BaseModel


class PricePointOut(BaseModel):
    t: int
    c: float


class PriceSeriesOut(BaseModel):
    ticker: str
    range: str
    currency: str | None
    points: list[PricePointOut]
    previous_close: float | None
    last: float | None
    change: float | None
    change_percent: float | None
