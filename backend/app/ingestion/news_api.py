"""Finnhub company-news adapter, the first non-SEC document source.

Finnhub's free tier covers `/company-news` (ticker-scoped, date-ranged) with
no cost, but it does require a key. When none is configured this adapter
returns nothing and logs once rather than raising: a missing optional source
must never take down an ingestion run that SEC EDGAR would otherwise complete
fine. That is the same posture the engine takes toward a missing LLM key.

One news item becomes one document. They are far shorter than a filing, so
there is no section extraction to do downstream, the whole item is the text.
Items with no summary body are skipped: a bare headline carries too little for
the engine to say anything defensible about, and would only dilute the feed.

**Relevance gating is the important part of this adapter.** Finnhub's
`/company-news` returns anything that merely *mentions* the ticker, which in
practice is mostly market-wrap pieces and aggregator listicles about other
companies. Measured against a live AAPL pull: 106 of 176 items never said
"Apple" at all, and 11 of the 15 most recent were about Nvidia, Alibaba, or
Samsung. Ingested unfiltered, the engine would have attributed those stories
to Apple. So an item is kept only when the company is named in its headline,
which is the deterministic version of "is this article actually about them".
"""

import html
import logging
import re
from datetime import datetime, timedelta, timezone

import httpx

from app.config import settings
from app.ingestion.base import DocumentSourceAdapter, RawDocumentDTO
from app.services.company_lookup import get_company_lookup_service

logger = logging.getLogger(__name__)

_COMPANY_NEWS_URL = "https://finnhub.io/api/v1/company-news"

# Finnhub's free tier rejects ranges longer than roughly a year, and a first
# ingest has no `since` to work from, so bound the default lookback.
DEFAULT_LOOKBACK_DAYS = 60

# Below this, an item is a headline with no substance behind it.
_MIN_SUMMARY_CHARS = 120

# Legal-form words that are never how a headline refers to a company:
# "Apple Inc." is written "Apple", "Micron Technology, Inc." is "Micron".
_CORPORATE_SUFFIXES = re.compile(
    r"\b(incorporated|inc|corporation|corp|company|co|limited|ltd|plc|holdings?|"
    r"group|technologies|technology|llc|lp|nv|sa|ag|se|trust)\b\.?",
    re.IGNORECASE,
)


def _core_name(company_name: str) -> str:
    """'Apple Inc.' -> 'apple'. The form a headline would actually use."""
    cleaned = _CORPORATE_SUFFIXES.sub(" ", company_name)
    cleaned = re.sub(r"[^\w\s]", " ", cleaned)
    core = " ".join(cleaned.split()).lower()
    # Stripping can leave nothing useful behind (a company literally named
    # "Technology Group"); the unstripped name is the safer fallback.
    return core if len(core) >= 3 else company_name.strip().lower()


def is_about_company(headline: str, *, company_name: str, ticker: str) -> bool:
    """True when the headline actually names this company.

    Headline only, deliberately. A body-only mention is nearly always a passing
    comparison ("unlike Apple, ..."), and in the same live sample only 9 of 176
    items mentioned the company in the body but not the headline, so the
    stricter rule costs very little and removes 60% of the noise.
    """
    if not headline:
        return False
    if _core_name(company_name) in headline.lower():
        return True
    # Tickers appear uppercase and often parenthesised, e.g. "(AAPL)". Matching
    # case-sensitively on a word boundary stops a short ticker like "F" from
    # matching every other word in the headline.
    return re.search(rf"\b{re.escape(ticker.upper())}\b", headline) is not None


class FinnhubNewsAdapter(DocumentSourceAdapter):
    source_name = "finnhub"
    source_type = "news_api"

    def __init__(self, lookback_days: int = DEFAULT_LOOKBACK_DAYS):
        self.lookback_days = lookback_days
        self._client = httpx.Client(timeout=30.0)

    @property
    def available(self) -> bool:
        return bool(settings.finnhub_api_key)

    def fetch(self, ticker: str, since: datetime | None = None) -> list[RawDocumentDTO]:
        if not self.available:
            logger.info("Finnhub: no API key configured, skipping news for %s.", ticker)
            return []

        now = datetime.now(timezone.utc)
        start = since or (now - timedelta(days=self.lookback_days))

        # Same cached SEC directory the EDGAR adapter uses, so this costs no
        # extra network call in a normal run.
        info = get_company_lookup_service().lookup(ticker)
        company_name = info.name if info else ticker

        items = self._fetch_news(ticker, start=start, end=now)
        documents: list[RawDocumentDTO] = []
        skipped = 0
        for item in items:
            if not is_about_company(
                item.get("headline") or "", company_name=company_name, ticker=ticker
            ):
                skipped += 1
                continue
            dto = self._to_document(ticker, item)
            if dto is not None:
                documents.append(dto)

        if skipped:
            logger.info(
                "Finnhub: kept %d of %d items for %s, %d were about other companies.",
                len(documents), len(items), ticker, skipped,
            )
        return documents

    def _fetch_news(self, ticker: str, *, start: datetime, end: datetime) -> list[dict]:
        params = {
            "symbol": ticker.upper(),
            "from": start.date().isoformat(),
            "to": end.date().isoformat(),
            "token": settings.finnhub_api_key,
        }
        resp = self._client.get(_COMPANY_NEWS_URL, params=params)
        if resp.status_code == 429:
            logger.warning("Finnhub: rate limited on %s, skipping this run.", ticker)
            return []
        if resp.status_code != 200:
            logger.warning("Finnhub: news fetch failed (%s) for %s", resp.status_code, ticker)
            return []

        payload = resp.json()
        if not isinstance(payload, list):
            logger.warning("Finnhub: unexpected payload shape for %s", ticker)
            return []
        return payload

    @staticmethod
    def _to_document(ticker: str, item: dict) -> RawDocumentDTO | None:
        # Finnhub returns HTML-encoded text, so a headline arrives as
        # "Storage &amp; Peripherals". Left as-is it renders literally in the
        # UI and, worse, reaches the model as an entity rather than a word.
        headline = html.unescape(item.get("headline") or "").strip()
        summary = html.unescape(item.get("summary") or "").strip()
        if not headline or len(summary) < _MIN_SUMMARY_CHARS:
            return None

        published_at = None
        epoch = item.get("datetime")
        if isinstance(epoch, (int, float)) and epoch > 0:
            published_at = datetime.fromtimestamp(epoch, tz=timezone.utc)

        return RawDocumentDTO(
            company_ticker=ticker,
            source_type=FinnhubNewsAdapter.source_type,
            source_name=FinnhubNewsAdapter.source_name,
            source_url=item.get("url"),
            doc_subtype="news",
            title=headline,
            published_at=published_at,
            # The engine reads one flat string per document; keeping the
            # headline inline means the model sees the framing, not just the
            # body, which is often where the actual claim lives.
            raw_text=f"{headline}\n\n{summary}",
            metadata={
                "publisher": item.get("source"),
                "category": item.get("category"),
                "finnhub_id": item.get("id"),
            },
        )
