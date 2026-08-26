"""Price history for the chart panel.

Thin by design: all provider knowledge lives in ingestion/prices.py. A ticker
with no available series returns 404 rather than an empty chart, so the client
can skip it in a rotation instead of rendering a blank panel.
"""

from fastapi import APIRouter, HTTPException, Query

from app.api.deps import CompanyRepo
from app.ingestion.prices import RANGE_SPECS, get_price_source
from app.schemas.price import PriceSeriesOut

router = APIRouter(tags=["prices"])


@router.get("/ranges", response_model=list[str])
def list_ranges():
    """The timescales the server can serve, so the client never hardcodes them."""
    return list(RANGE_SPECS)


@router.get("/companies/{ticker}/prices", response_model=PriceSeriesOut)
def company_prices(
    ticker: str,
    company_repo: CompanyRepo,
    range: str = Query(default="24H", description="One of the values from /ranges"),
):
    company = company_repo.get_by_ticker(ticker)
    if company is None:
        raise HTTPException(status_code=404, detail=f"Unknown ticker {ticker!r}")

    series = get_price_source().get(company.ticker, range)
    if series is None:
        raise HTTPException(status_code=404, detail="No price data available for this ticker")

    return PriceSeriesOut(
        ticker=series.ticker,
        range=series.range,
        currency=series.currency,
        points=[{"t": p.t, "c": p.c} for p in series.points],
        previous_close=series.previous_close,
        last=series.last,
        change=series.change,
        change_percent=series.change_percent,
    )
