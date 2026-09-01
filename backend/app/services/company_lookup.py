"""Resolves a ticker to a real company (name, CIK) on demand.

This is what lets "add ticker" in the UI accept any real ticker directly,
rather than requiring every company to be hand-seeded via a CLI script
first. Uses the same public, free, keyless SEC EDGAR ticker directory the
ingestion adapter needs anyway (see app/ingestion/sec_edgar.py), this
service is the single place that fetches and caches it, so the adapter
and the "add ticker" flow share one lookup instead of duplicating it.
"""

import logging
import threading
from dataclasses import dataclass
from functools import lru_cache

import httpx

from app.config import settings
from app.ingestion.rate_limit import limiter

logger = logging.getLogger(__name__)

_TICKER_LOOKUP_URL = "https://www.sec.gov/files/company_tickers.json"


class SecAccessDenied(RuntimeError):
    """SEC rejected our User-Agent. Distinct from "ticker not found" because
    the fix is a config change, not a different ticker."""


@dataclass
class CompanyInfo:
    ticker: str
    name: str
    cik: str


class CompanyLookupService:
    def __init__(self):
        self._client = httpx.Client(
            headers={"User-Agent": settings.sec_edgar_user_agent}, timeout=30.0
        )
        self._cache: dict[str, CompanyInfo] | None = None
        # This service is a process-wide singleton and ingestion now runs
        # tickers concurrently, so the lazy load is a race: without the lock
        # every worker sees an empty cache at once and each downloads the same
        # megabyte of ticker directory, which is both wasteful and the fastest
        # way to earn a rate-limit block on the very first request of a run.
        self._load_lock = threading.Lock()

    def _ensure_loaded(self) -> dict[str, CompanyInfo]:
        if self._cache is not None:
            return self._cache

        with self._load_lock:
            # Re-checked inside the lock: the thread that waited here has
            # nothing left to fetch.
            if self._cache is not None:
                return self._cache

            limiter.acquire(_TICKER_LOOKUP_URL)
            resp = self._client.get(_TICKER_LOOKUP_URL)
            resp.raise_for_status()
            data = resp.json()
            self._cache = {
                entry["ticker"].upper(): CompanyInfo(
                    ticker=entry["ticker"].upper(),
                    name=entry["title"],
                    cik=str(entry["cik_str"]).zfill(10),
                )
                for entry in data.values()
            }
        return self._cache

    def lookup(self, ticker: str) -> CompanyInfo | None:
        try:
            return self._ensure_loaded().get(ticker.upper())
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 403:
                # The single most confusing failure this app can have: SEC
                # refuses the User-Agent, every lookup fails, and the user is
                # told their ticker does not exist. Name the real cause.
                raise SecAccessDenied(
                    "SEC refused the request. This is the User-Agent, not the ticker: "
                    "SEC requires it to contain a contact email address and to contain "
                    "no URL. Set SEC_EDGAR_USER_AGENT to something like "
                    "'Your Name your-email@example.com'. "
                    f"Currently sending: {settings.sec_edgar_user_agent!r}"
                ) from exc
            logger.exception("Company lookup failed while resolving %s", ticker)
            return None
        except httpx.HTTPError:
            logger.exception("Company lookup failed while resolving %s", ticker)
            return None


@lru_cache
def get_company_lookup_service() -> CompanyLookupService:
    return CompanyLookupService()
