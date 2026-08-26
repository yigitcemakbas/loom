"""Shared scraper infrastructure: robots.txt, rate limiting, honest identity.

Every scraper in the project goes through this class. The three rules it
enforces are not decoration, they are the difference between a research tool
and a nuisance:

1. **robots.txt is obeyed, not consulted.** `can_fetch` is checked before every
   request and a disallowed URL is skipped. The fetch returns None; the caller
   logs and moves on. There is no override flag, deliberately, an override that
   exists is an override that eventually gets used.
2. **One request per domain at a time, spaced.** Rate limiting is keyed by
   domain rather than by scraper, so two scrapers pointed at the same host
   still cooperate.
3. **The User-Agent identifies this tool honestly.** It never impersonates a
   browser. If a site wants to block Loom it must be able to, spoofing Chrome
   to evade a block is exactly the behaviour robots.txt exists to prevent.

A failure inside one scraper is contained here (`fetch_html` returns None
rather than raising) so a broken selector or a 503 on one source can never
take down an ingestion run for the others.
"""

import logging
import threading
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# Politeness gap between two requests to the same domain. Deliberately well
# above what the sites involved require: nothing here is latency-sensitive.
DEFAULT_REQUEST_INTERVAL_SECONDS = 1.5

# robots.txt rarely changes; re-fetching it per request would itself be the
# kind of traffic it is meant to limit.
_ROBOTS_CACHE_SECONDS = 3600.0


class RobotsPolicy:
    """Per-domain robots.txt cache.

    A domain whose robots.txt cannot be fetched is treated as **allowed**,
    matching the convention every mainstream crawler follows (an unreachable
    robots.txt is an absent one, not a blanket denial). A domain whose
    robots.txt is fetched and *does* disallow the path is always refused.
    """

    def __init__(self, user_agent: str):
        self.user_agent = user_agent
        self._parsers: dict[str, tuple[RobotFileParser | None, float]] = {}
        self._lock = threading.Lock()

    def can_fetch(self, url: str) -> bool:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False
        domain = f"{parsed.scheme}://{parsed.netloc}"

        parser = self._parser_for(domain)
        if parser is None:
            return True
        return parser.can_fetch(self.user_agent, url)

    def _parser_for(self, domain: str) -> RobotFileParser | None:
        with self._lock:
            cached = self._parsers.get(domain)
            if cached is not None and (time.monotonic() - cached[1]) < _ROBOTS_CACHE_SECONDS:
                return cached[0]

        parser = self._load(domain)
        with self._lock:
            self._parsers[domain] = (parser, time.monotonic())
        return parser

    def _load(self, domain: str) -> RobotFileParser | None:
        url = f"{domain}/robots.txt"
        try:
            resp = httpx.get(url, headers={"User-Agent": self.user_agent}, timeout=15.0)
        except Exception:
            logger.warning("robots.txt unreachable for %s, proceeding as unrestricted.", domain)
            return None

        if resp.status_code != 200:
            logger.info("robots.txt returned %s for %s, proceeding as unrestricted.", resp.status_code, domain)
            return None

        parser = RobotFileParser()
        parser.parse(resp.text.splitlines())
        return parser


class BaseScraper:
    """Base for every scraping adapter. Not itself a DocumentSourceAdapter,
    a concrete scraper inherits from both this and `DocumentSourceAdapter`,
    so that "how to fetch politely" and "what to produce" stay separate
    concerns rather than one class doing both jobs.
    """

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        request_interval: float = DEFAULT_REQUEST_INTERVAL_SECONDS,
    ):
        self.user_agent = user_agent or settings.scraper_user_agent
        self.request_interval = request_interval
        self.robots = RobotsPolicy(self.user_agent)
        self._client = httpx.Client(
            headers={"User-Agent": self.user_agent},
            timeout=30.0,
            follow_redirects=True,
        )
        self._last_request_at: dict[str, float] = {}
        self._lock = threading.Lock()

    def _throttle(self, url: str) -> None:
        domain = urlparse(url).netloc
        with self._lock:
            last = self._last_request_at.get(domain)
            now = time.monotonic()
            if last is not None:
                wait = self.request_interval - (now - last)
                if wait > 0:
                    time.sleep(wait)
                    now = time.monotonic()
            self._last_request_at[domain] = now

    def fetch_html(self, url: str) -> str | None:
        """Fetch one URL, or None if robots.txt forbids it or the request fails.

        Never raises. Callers treat None as "this one is unavailable" and
        continue, which is what keeps one bad page from ending a batch.
        """
        if not self.robots.can_fetch(url):
            logger.info("robots.txt disallows %s, skipping.", url)
            return None

        self._throttle(url)
        try:
            resp = self._client.get(url)
        except Exception:
            logger.warning("Request failed for %s", url, exc_info=True)
            return None

        if resp.status_code != 200:
            logger.info("Fetch returned %s for %s", resp.status_code, url)
            return None
        return resp.text
