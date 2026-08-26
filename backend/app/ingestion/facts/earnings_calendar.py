"""Scheduled earnings dates and consensus estimates, from Finnhub's free tier.

Until this existed, Loom had no concept of *when* anything happens. It could
tell you what a company disclosed last quarter but not that the company reports
in six days, which is the moment its output is worth most: an investor decides
around events, and the days before an earnings call are when the accumulated
evidence actually has to be weighed.

Both directions matter and both are stored:

  forward   the next scheduled date, and what the market expects of it
  backward  past dates with what was actually delivered, which is what makes a
            beat/miss record computable rather than anecdotal

Estimates get revised as a date approaches. A revision produces a new row
rather than overwriting the old one, so the record shows how expectations moved
in the run-up, which is itself information. Readers that want the current
consensus take the most recently fetched row for that quarter.
"""

import logging
from datetime import date, datetime, timedelta, timezone

import httpx

from app.config import settings
from app.ingestion.base import FactSourceAdapter, StructuredFactDTO

logger = logging.getLogger(__name__)

_CALENDAR_URL = "https://finnhub.io/api/v1/calendar/earnings"

# Far enough back for a beat/miss record, far enough forward to see the next
# scheduled report for any company on a normal quarterly cadence.
DEFAULT_LOOKBACK_DAYS = 400
DEFAULT_LOOKAHEAD_DAYS = 120

# Finnhub's own codes for when a company reports relative to the session.
_HOUR_LABELS = {
    "bmo": "before market open",
    "amc": "after market close",
    "dmh": "during market hours",
}


class EarningsCalendarAdapter(FactSourceAdapter):
    source_name = "finnhub-earnings-calendar"
    source_type = "earnings_event"

    def __init__(
        self,
        lookback_days: int = DEFAULT_LOOKBACK_DAYS,
        lookahead_days: int = DEFAULT_LOOKAHEAD_DAYS,
    ):
        self.lookback_days = lookback_days
        self.lookahead_days = lookahead_days
        self._client = httpx.Client(timeout=30.0)

    @property
    def available(self) -> bool:
        return bool(settings.finnhub_api_key)

    def fetch(self, ticker: str, since: datetime | None = None) -> list[StructuredFactDTO]:
        if not self.available:
            logger.info("Earnings calendar: no Finnhub key configured, skipping %s.", ticker)
            return []

        today = date.today()
        # `since` is deliberately ignored for the forward window: an incremental
        # ingest must still see the *upcoming* date, which by definition is
        # newer than anything already stored.
        start = today - timedelta(days=self.lookback_days)
        end = today + timedelta(days=self.lookahead_days)

        events = self._fetch_calendar(ticker, start=start, end=end)
        facts: list[StructuredFactDTO] = []
        for event in events:
            dto = self._to_fact(ticker, event)
            if dto is not None:
                facts.append(dto)
        return facts

    def _fetch_calendar(self, ticker: str, *, start: date, end: date) -> list[dict]:
        params = {
            "from": start.isoformat(),
            "to": end.isoformat(),
            "symbol": ticker.upper(),
            "token": settings.finnhub_api_key,
        }
        try:
            resp = self._client.get(_CALENDAR_URL, params=params)
        except Exception:
            logger.warning("Earnings calendar: request failed for %s", ticker, exc_info=True)
            return []

        if resp.status_code == 429:
            logger.warning("Earnings calendar: rate limited on %s.", ticker)
            return []
        if resp.status_code != 200:
            logger.warning(
                "Earnings calendar: fetch failed (%s) for %s", resp.status_code, ticker
            )
            return []

        payload = resp.json()
        events = payload.get("earningsCalendar") if isinstance(payload, dict) else None
        return events or []

    @staticmethod
    def _to_fact(ticker: str, event: dict) -> StructuredFactDTO | None:
        raw_date = event.get("date")
        if not raw_date:
            return None
        try:
            when = datetime.strptime(raw_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

        eps_estimate = event.get("epsEstimate")
        eps_actual = event.get("epsActual")
        revenue_estimate = event.get("revenueEstimate")
        revenue_actual = event.get("revenueActual")

        # An event is "reported" once an actual exists; before that it is
        # scheduled, and the distinction drives everything downstream.
        reported = eps_actual is not None or revenue_actual is not None

        surprise_pct = None
        if reported and eps_actual is not None and eps_estimate:
            try:
                surprise_pct = round((float(eps_actual) - float(eps_estimate)) / abs(float(eps_estimate)) * 100, 2)
            except (TypeError, ValueError, ZeroDivisionError):
                surprise_pct = None

        hour = (event.get("hour") or "").lower()

        return StructuredFactDTO(
            company_ticker=ticker,
            fact_type="earnings_event",
            source_name=EarningsCalendarAdapter.source_name,
            as_of_date=when,
            # The headline number the market prices against before the event.
            value=float(eps_estimate) if eps_estimate is not None else None,
            unit="eps_usd",
            attributes={
                "quarter": event.get("quarter"),
                "fiscal_year": event.get("year"),
                "eps_estimate": eps_estimate,
                "eps_actual": eps_actual,
                "revenue_estimate": revenue_estimate,
                "revenue_actual": revenue_actual,
                "eps_surprise_percent": surprise_pct,
                "reported": reported,
                "hour": hour,
                "hour_label": _HOUR_LABELS.get(hour),
            },
        )
