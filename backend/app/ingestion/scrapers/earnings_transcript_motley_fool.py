"""Earnings call transcripts from fool.com, the project's first scraper.

Transcripts are the highest-value text source in the plan that has no API
behind it: unlike a filing, a call is where management answers questions it
did not choose, so tone and hedging are visible in a way a drafted document
never shows.

**Discovery.** There is no per-ticker transcript index that survives without
JavaScript, but fool.com publishes monthly sitemaps (linked from its own
robots.txt, so fetching them is explicitly sanctioned) listing every URL
published that month. Transcript URLs carry the ticker and date in the slug:

    /earnings/call-transcripts/2026/08/07/apple-aapl-q3-2026-earnings-call-transcript/

So one sitemap fetch per month yields (ticker, date, url) for every transcript
that month, and only the URLs matching the requested ticker are then fetched.
The parsed index is cached on the instance, which matters because ingestion
runs many tickers against one scraper: without it, a ten-ticker run would pull
the same sitemaps ten times over. The cache is built under a lock, because
concurrent ingestion reintroduces that exact waste in a harder-to-see form,
every worker finding the cache empty at the same moment and fetching the whole
sitemap set in parallel.

**Politeness** is inherited wholesale from BaseScraper: robots.txt is checked
before every request including the sitemaps, requests to the domain are spaced,
and the User-Agent identifies Loom rather than impersonating a browser.
"""

import logging
import re
import threading
from datetime import datetime, timezone

from selectolax.parser import HTMLParser

from app.ingestion.base import DocumentSourceAdapter, RawDocumentDTO
from app.ingestion.scrapers.base_scraper import BaseScraper

logger = logging.getLogger(__name__)

_SITEMAP_URL = "https://www.fool.com/sitemap/{year}/{month:02d}"

# .../call-transcripts/<yyyy>/<mm>/<dd>/<company>-<ticker>-q<n>-<year>-earnings-call-transcript/
_TRANSCRIPT_URL_RE = re.compile(
    r"https://www\.fool\.com/earnings/call-transcripts/"
    r"(?P<year>\d{4})/(?P<month>\d{2})/(?P<day>\d{2})/"
    r"(?P<slug>[a-z0-9-]+?)-(?P<ticker>[a-z]{1,5})"
    r"-q(?P<quarter>\d)-(?P<fiscal_year>\d{4})-earnings-call-transcript/?"
)

# How many months back to look when no `since` is given. Transcripts land
# quarterly, so this reliably covers the two most recent calls.
DEFAULT_LOOKBACK_MONTHS = 8

# Boilerplate blocks inside the transcript container that are not transcript.
_NOISE_SELECTOR = "script, style, .article-body-promobox, figure, aside"

# Shorter than this and the page is a stub or a paywall interstitial, not a
# real transcript worth storing.
_MIN_TRANSCRIPT_CHARS = 2000


class MotleyFoolTranscriptScraper(BaseScraper, DocumentSourceAdapter):
    source_name = "fool.com"
    source_type = "scraped_transcript"

    def __init__(self, lookback_months: int = DEFAULT_LOOKBACK_MONTHS, **kwargs):
        super().__init__(**kwargs)
        self.lookback_months = lookback_months
        self._index: dict[str, list[dict]] | None = None
        self._index_lock = threading.Lock()

    def fetch(self, ticker: str, since: datetime | None = None) -> list[RawDocumentDTO]:
        index = self._transcript_index()
        entries = index.get(ticker.upper(), [])
        if not entries:
            logger.info("fool.com: no transcripts found for %s in the scanned window.", ticker)
            return []

        documents: list[RawDocumentDTO] = []
        for entry in entries:
            if since is not None and entry["published_at"] < since:
                continue
            dto = self._fetch_transcript(ticker, entry)
            if dto is not None:
                documents.append(dto)
        return documents

    # ---- discovery -----------------------------------------------------

    def _transcript_index(self) -> dict[str, list[dict]]:
        """Ticker -> transcript entries, newest first. Built once per instance.

        The lock is what makes "once" true when tickers are ingested
        concurrently. Without it every worker starts with `self._index` still
        None and fetches the full set of monthly sitemaps itself, which for
        eight months and eight workers is sixty-four requests to one domain to
        obtain one index. Since those requests are also correctly rate limited,
        the redundant work does not merely waste bandwidth, it becomes the
        slowest part of the entire run.
        """
        if self._index is not None:
            return self._index

        with self._index_lock:
            # Whoever waited here has nothing left to fetch.
            if self._index is not None:
                return self._index
            return self._build_index()

    def _build_index(self) -> dict[str, list[dict]]:
        index: dict[str, list[dict]] = {}
        for year, month in self._months_to_scan():
            xml = self.fetch_html(_SITEMAP_URL.format(year=year, month=month))
            if xml is None:
                continue
            for entry in self._parse_sitemap(xml):
                index.setdefault(entry["ticker"], []).append(entry)

        for entries in index.values():
            entries.sort(key=lambda e: e["published_at"], reverse=True)
            # The same transcript appears more than once in a sitemap (canonical
            # plus syndicated paths); one document per URL is the point.
            seen: set[str] = set()
            entries[:] = [e for e in entries if not (e["url"] in seen or seen.add(e["url"]))]

        self._index = index
        return index

    def _months_to_scan(self) -> list[tuple[int, int]]:
        now = datetime.now(timezone.utc)
        months: list[tuple[int, int]] = []
        year, month = now.year, now.month
        for _ in range(self.lookback_months):
            months.append((year, month))
            month -= 1
            if month == 0:
                year, month = year - 1, 12
        return months

    @staticmethod
    def _parse_sitemap(xml: str) -> list[dict]:
        entries: list[dict] = []
        for match in _TRANSCRIPT_URL_RE.finditer(xml):
            published_at = datetime(
                int(match.group("year")),
                int(match.group("month")),
                int(match.group("day")),
                tzinfo=timezone.utc,
            )
            entries.append(
                {
                    "ticker": match.group("ticker").upper(),
                    "url": match.group(0),
                    "published_at": published_at,
                    "quarter": f"Q{match.group('quarter')} {match.group('fiscal_year')}",
                }
            )
        return entries

    # ---- retrieval -----------------------------------------------------

    def _fetch_transcript(self, ticker: str, entry: dict) -> RawDocumentDTO | None:
        html = self.fetch_html(entry["url"])
        if html is None:
            return None

        text = self._extract_transcript(html)
        if text is None:
            logger.info("fool.com: no transcript body found at %s", entry["url"])
            return None

        return RawDocumentDTO(
            company_ticker=ticker,
            source_type=self.source_type,
            source_name=self.source_name,
            source_url=entry["url"],
            doc_subtype="earnings_call",
            title=f"{ticker.upper()} {entry['quarter']} earnings call transcript",
            published_at=entry["published_at"],
            raw_text=text,
            metadata={"quarter": entry["quarter"]},
        )

    @classmethod
    def _extract_transcript(cls, html: str) -> str | None:
        """Pull the transcript body out of the article page.

        Scoped to the transcript container rather than the whole body: the page
        around it is navigation, related-article rails, and promo boxes, all of
        which would otherwise be fed to the model as if management had said it.
        """
        tree = HTMLParser(html)
        container = tree.css_first(".transcript-content") or tree.css_first(".article-body")
        if container is None:
            return None

        for node in container.css(_NOISE_SELECTOR):
            node.decompose()

        text = container.text(separator="\n", strip=True)
        if len(text) < _MIN_TRANSCRIPT_CHARS:
            return None
        return text
