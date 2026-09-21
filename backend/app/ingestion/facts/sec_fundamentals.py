"""Reported financials from SEC XBRL: what the company actually earned.

The largest gap the project carried. Every judgement Loom formed before this
was built from prose and market positioning, with no access to revenue,
margin, cash or leverage. A risk factor about margin pressure could be read
and weighed without ever checking whether margins had in fact moved.

**Two access patterns, deliberately both.** `companyfacts` returns one
company's entire reported history, which is depth and costs a request per
company. `frames` returns one concept for every filer in a period in a single
request, which is breadth and is what makes ranking a company against its peers
affordable across hundreds of names. Depth for the few, breadth for the many,
mirroring the coverage tiers.

**`filed`, not `end`.** Each XBRL fact carries both the period it describes and
the date it was reported. Storing a quarter's revenue against the quarter's end
date would make it appear knowable weeks before anyone could have known it,
which is the exact lookahead that makes a backtest look brilliant and a live
strategy lose. The reported date is the one that goes in `as_of_date`; the
period end is kept alongside it.
"""

import logging
from datetime import datetime, timezone
from typing import Iterable, Optional

import httpx

from app.config import settings
from app.ingestion.base import FactSourceAdapter, StructuredFactDTO
from app.ingestion.rate_limit import limiter
from app.services.company_lookup import get_company_lookup_service

logger = logging.getLogger(__name__)

_COMPANYFACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik10}.json"
_FRAMES_URL = "https://data.sec.gov/api/xbrl/frames/us-gaap/{concept}/USD/{period}.json"

# XBRL concept names drift as accounting standards change, so one metric needs
# several aliases. Apple reports revenue under
# RevenueFromContractWithCustomerExcludingAssessedTax in recent years and
# Revenues in older ones; taking only the first name found would silently
# produce a history with a hole in the middle.
CONCEPTS: dict[str, tuple[str, ...]] = {
    "revenue": (
        "RevenueFromContractWithCustomerExcludingAssessedTax",
        "Revenues",
        "SalesRevenueNet",
    ),
    "gross_profit": ("GrossProfit",),
    "operating_income": ("OperatingIncomeLoss",),
    "net_income": ("NetIncomeLoss", "ProfitLoss"),
    "rnd_expense": ("ResearchAndDevelopmentExpense",),
    "assets": ("Assets",),
    "liabilities": ("Liabilities",),
    "equity": ("StockholdersEquity",),
    "cash": (
        "CashAndCashEquivalentsAtCarryingValue",
        "CashCashEquivalentsRestrictedCashAndRestrictedCashEquivalents",
    ),
    "operating_cash_flow": ("NetCashProvidedByUsedInOperatingActivities",),
    "eps_diluted": ("EarningsPerShareDiluted",),
    "shares_diluted": ("WeightedAverageNumberOfDilutedSharesOutstanding",),
}

# How far back to store. Enough to compute multi-year trends without importing
# two decades of history nothing will read.
DEFAULT_YEARS = 4

# How a fact's period is classified. XBRL publishes quarterly and cumulative
# year-to-date figures under the same concept name, so Apple's "revenue" for a
# filing can be either three months or nine depending on the row. Comparing one
# company's quarter against another's year-to-date is silently wrong and looks
# entirely plausible, which is the same failure the earnings extractor hit with
# a "Twelve Months Ended" column.
_QUARTER_DAYS = (80, 100)
_HALF_DAYS = (170, 190)
_NINE_MONTH_DAYS = (260, 285)
_YEAR_DAYS = (350, 380)

# Annual and quarterly reports only. XBRL also carries 8-K and S-1 facts, which
# duplicate or pre-date the audited figures.
ACCEPTED_FORMS = {"10-K", "10-Q", "20-F", "40-F"}


class SecFundamentalsAdapter(FactSourceAdapter):
    """Per-company reported financials. Free, keyless, and point-in-time."""

    source_name = "sec-xbrl"
    source_type = "fundamental"

    def __init__(self, years: int = DEFAULT_YEARS, concepts: Optional[dict] = None):
        self.years = years
        self.concepts = concepts or CONCEPTS
        self._client = httpx.Client(
            headers={"User-Agent": settings.sec_edgar_user_agent},
            timeout=60.0,   # companyfacts runs to several megabytes.
            follow_redirects=True,
        )

    def fetch(self, ticker: str, since: Optional[datetime] = None) -> list[StructuredFactDTO]:
        info = get_company_lookup_service().lookup(ticker)
        if info is None:
            logger.warning("SEC XBRL: no CIK for %s", ticker)
            return []

        url = _COMPANYFACTS_URL.format(cik10=info.cik)
        try:
            limiter.acquire(url)
            response = self._client.get(url)
        except Exception:
            logger.warning("SEC XBRL: request failed for %s", ticker, exc_info=True)
            return []

        if response.status_code != 200:
            # A company with no XBRL history is normal, not an error: ETFs and
            # recent listings frequently have none.
            logger.info("SEC XBRL: %s returned %s for %s", url, response.status_code, ticker)
            return []

        try:
            payload = response.json()
        except Exception:
            logger.warning("SEC XBRL: unparseable payload for %s", ticker, exc_info=True)
            return []

        return list(self._to_facts(ticker, payload, since))

    def _to_facts(self, ticker: str, payload: dict, since) -> Iterable[StructuredFactDTO]:
        gaap = (payload.get("facts") or {}).get("us-gaap") or {}
        cutoff_year = datetime.now(timezone.utc).year - self.years

        for metric, aliases in self.concepts.items():
            for concept in aliases:
                entry = gaap.get(concept)
                if not entry:
                    continue

                for unit, observations in (entry.get("units") or {}).items():
                    for obs in observations:
                        fact = self._to_fact(ticker, metric, concept, unit, obs, cutoff_year, since)
                        if fact is not None:
                            yield fact
                # Aliases are ordered by preference; once one has produced
                # data, the rest describe the same line under an older name.
                break

    @staticmethod
    def _classify_period(start: Optional[str], end: Optional[str]) -> tuple[Optional[str], Optional[int]]:
        """Name the span a fact covers, so a quarter is never read as a year.

        Balance sheet lines (assets, cash) carry no start date because they are
        a position at an instant rather than a flow over a period; those are
        labelled accordingly rather than forced into a duration.
        """
        if not end:
            return None, None
        if not start:
            return "instant", 0

        try:
            days = (
                datetime.strptime(end, "%Y-%m-%d") - datetime.strptime(start, "%Y-%m-%d")
            ).days
        except ValueError:
            return None, None

        for label, (low, high) in (
            ("quarter", _QUARTER_DAYS),
            ("half_year", _HALF_DAYS),
            ("nine_months", _NINE_MONTH_DAYS),
            ("year", _YEAR_DAYS),
        ):
            if low <= days <= high:
                return label, days
        return "other", days

    def _to_fact(self, ticker, metric, concept, unit, obs, cutoff_year, since):
        form = obs.get("form")
        if form not in ACCEPTED_FORMS:
            return None

        filed = obs.get("filed")
        value = obs.get("val")
        if not filed or value is None:
            return None

        try:
            filed_at = datetime.strptime(filed, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None

        if filed_at.year < cutoff_year:
            return None
        if since is not None and filed_at < since:
            return None

        period, period_days = self._classify_period(obs.get("start"), obs.get("end"))

        return StructuredFactDTO(
            company_ticker=ticker,
            fact_type="fundamental",
            source_name=self.source_name,
            # The date the figure became public, never the period it covers.
            as_of_date=filed_at,
            value=float(value),
            unit=unit,
            source_url=f"https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK={ticker}",
            attributes={
                "metric": metric,
                "concept": concept,
                "period_start": obs.get("start"),
                "period_end": obs.get("end"),
                # "quarter", "year", "instant", ... Never assume a bare value
                # is a quarter; filter on this before comparing companies.
                "period": period,
                "period_days": period_days,
                "fiscal_year": obs.get("fy"),
                "fiscal_period": obs.get("fp"),
                "form": form,
                "accession": obs.get("accn"),
            },
        )


def fetch_peer_frame(concept: str, period: str) -> dict[str, float]:
    """One concept for every filer in a period, in a single request.

    This is the cheap half. Ranking a company against its peers needs the same
    line from hundreds of filers, and fetching that per company would be
    hundreds of multi-megabyte downloads. `frames` answers it in one, which is
    what makes cross-sectional work affordable at the scale the watch tier
    operates on.

    `period` is SEC's calendar notation, for example "CY2025Q4" for a quarter
    or "CY2025" for a year. Returns CIK (zero-padded) to value.
    """
    url = _FRAMES_URL.format(concept=concept, period=period)
    try:
        limiter.acquire(url)
        with httpx.Client(
            headers={"User-Agent": settings.sec_edgar_user_agent}, timeout=45.0
        ) as client:
            response = client.get(url)
    except Exception:
        logger.warning("SEC XBRL frames request failed for %s %s", concept, period, exc_info=True)
        return {}

    if response.status_code != 200:
        logger.info("SEC XBRL frames: %s %s returned %s", concept, period, response.status_code)
        return {}

    try:
        data = response.json().get("data") or []
    except Exception:
        return {}

    return {str(row["cik"]).zfill(10): float(row["val"]) for row in data if row.get("val") is not None}
