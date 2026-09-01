"""FINRA consolidated short interest.

Short interest is the one widely-followed measure of positioning *against* a
company, and it is the missing input to `engine.fact_rules.short_interest_spike`,
which has been written and tested since Phase 5 but could never fire without a
source feeding it.

FINRA publishes the consolidated figures through a free, keyless query API. Two
practical notes about that API, both discovered by using it:

- It returns **CSV**, not JSON, despite being a JSON-request endpoint. The
  `Accept: text/plain` header and a `csv.DictReader` are deliberate, not an
  oversight.
- Results cannot be sorted server-side unless the partition key
  (`settlementDate`) is also filtered on with an EQUAL comparison, which is
  useless for a date range. Ordering is therefore done here after the fetch.

Reporting is bi-monthly rather than daily, so a full history for one ticker is
a couple of hundred rows and one request.
"""

import logging
from datetime import datetime, timedelta, timezone
from io import StringIO
import csv

import httpx

from app.config import settings
from app.ingestion.base import FactSourceAdapter, StructuredFactDTO

logger = logging.getLogger(__name__)

_URL = "https://api.finra.org/data/group/otcMarket/name/consolidatedShortInterest"

# Bounded by date rather than by row count. `limit` alone truncates from the
# OLDEST end, so a plain limit silently drops the most recent settlement dates,
# which are the only ones any rule cares about. Measured: limit=200 on NVDA
# returned a "latest" reading four months stale.
DEFAULT_LOOKBACK_DAYS = 730

# Ceiling on rows returned once the date filter has already narrowed the set.
# Bi-monthly reporting puts two years at roughly 48 rows.
_MAX_ROWS = 200


class FinraShortInterestAdapter(FactSourceAdapter):
    source_name = "finra"
    source_type = "short_interest"

    def __init__(self, lookback_days: int = DEFAULT_LOOKBACK_DAYS):
        self.lookback_days = lookback_days
        self._client = httpx.Client(
            headers={
                "User-Agent": settings.scraper_user_agent,
                "Content-Type": "application/json",
                # The endpoint speaks CSV; asking for JSON returns an error body.
                "Accept": "text/plain",
            },
            timeout=45.0,
        )

    def fetch(self, ticker: str, since: datetime | None = None) -> list[StructuredFactDTO]:
        rows = self._fetch_rows(ticker)
        facts: list[StructuredFactDTO] = []
        for row in rows:
            dto = self._to_fact(ticker, row)
            if dto is None:
                continue
            if since is not None and dto.as_of_date < since:
                continue
            facts.append(dto)

        # Oldest first, so a reader of the stored series sees it in time order
        # and the spike rule compares the right pair.
        facts.sort(key=lambda f: f.as_of_date)
        return facts

    def _fetch_rows(self, ticker: str) -> list[dict]:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=self.lookback_days)).date()
        payload = {
            "limit": _MAX_ROWS,
            "compareFilters": [
                {"fieldName": "symbolCode", "fieldValue": ticker.upper(), "compareType": "EQUAL"},
                # Without this the row cap cuts off the newest readings, since
                # the API returns oldest-first and cannot be sorted unless the
                # partition key is filtered with EQUAL.
                {
                    "fieldName": "settlementDate",
                    "fieldValue": cutoff.isoformat(),
                    "compareType": "GTE",
                },
            ],
        }
        try:
            resp = self._client.post(_URL, json=payload)
        except Exception:
            logger.warning("FINRA: request failed for %s", ticker, exc_info=True)
            return []

        if resp.status_code != 200:
            logger.info("FINRA: short interest fetch returned %s for %s", resp.status_code, ticker)
            return []

        text = resp.text.strip()
        if not text or text.startswith("{"):
            # An error body comes back as JSON even when CSV was requested.
            logger.info("FINRA: no short interest rows for %s", ticker)
            return []

        try:
            return list(csv.DictReader(StringIO(text)))
        except csv.Error:
            logger.warning("FINRA: could not parse the CSV response for %s", ticker, exc_info=True)
            return []

    @staticmethod
    def _number(value: str | None) -> float | None:
        if value in (None, "", "null"):
            return None
        try:
            return float(value)
        except ValueError:
            return None

    @classmethod
    def _to_fact(cls, ticker: str, row: dict) -> StructuredFactDTO | None:
        settlement = (row.get("settlementDate") or "").strip()
        current = cls._number(row.get("currentShortPositionQuantity"))
        if not settlement or current is None:
            return None

        try:
            as_of = datetime.strptime(settlement, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

        previous = cls._number(row.get("previousShortPositionQuantity"))
        days_to_cover = cls._number(row.get("daysToCoverQuantity"))
        average_volume = cls._number(row.get("averageDailyVolumeQuantity"))

        # FINRA supplies its own change figure. Recomputing it here would risk
        # disagreeing with the published number over a stock split, which the
        # feed flags separately.
        change_percent = cls._number(row.get("changePercent"))

        return StructuredFactDTO(
            company_ticker=ticker,
            fact_type="short_interest",
            source_name=FinraShortInterestAdapter.source_name,
            as_of_date=as_of,
            # Shares short is the headline figure the spike rule compares.
            value=current,
            unit="shares",
            attributes={
                "previous_short_position": previous,
                "days_to_cover": days_to_cover,
                "average_daily_volume": average_volume,
                "change_percent": change_percent,
                "change_shares": cls._number(row.get("changePreviousNumber")),
                "stock_split_flag": (row.get("stockSplitFlag") or "").strip() or None,
                "market": (row.get("marketClassCode") or "").strip() or None,
                "settlement_date": settlement,
            },
        )
