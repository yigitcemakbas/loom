"""Scraper infrastructure and transcript-parsing tests.

The robots.txt tests matter most: a scraper that only claims to respect
robots.txt is worse than one that does not check at all, because it invites
trust it has not earned. These pin down that a disallowed path is actually
refused, using a parsed policy rather than the network.
"""

from datetime import datetime, timezone

from app.ingestion.scrapers.base_scraper import RobotsPolicy
from app.ingestion.scrapers.earnings_transcript_motley_fool import (
    MotleyFoolTranscriptScraper,
)

_ROBOTS = """
User-agent: *
Disallow: /account/
Disallow: /premium/
Allow: /earnings/
"""


def _policy() -> RobotsPolicy:
    """A RobotsPolicy with its fetch short-circuited: the parsing and decision
    logic is what is under test, not httpx."""
    from urllib.robotparser import RobotFileParser

    parser = RobotFileParser()
    parser.parse(_ROBOTS.splitlines())

    policy = RobotsPolicy("Loom test-agent")
    policy._parsers["https://example.com"] = (parser, float("inf"))
    return policy


def test_disallowed_path_is_refused():
    assert _policy().can_fetch("https://example.com/account/settings") is False


def test_allowed_path_is_permitted():
    assert _policy().can_fetch("https://example.com/earnings/call-transcripts/x/") is True


def test_unreachable_robots_is_treated_as_unrestricted():
    """An absent robots.txt is not a blanket denial, that is the convention
    every mainstream crawler follows."""
    policy = RobotsPolicy("Loom test-agent")
    policy._parsers["https://example.com"] = (None, float("inf"))

    assert policy.can_fetch("https://example.com/anything") is True


def test_malformed_url_is_refused():
    assert RobotsPolicy("Loom test-agent").can_fetch("not-a-url") is False


# ---- transcript discovery ----------------------------------------------

_SITEMAP = """<?xml version="1.0" encoding="utf-8"?>
<urlset>
<url><loc>https://www.fool.com/earnings/call-transcripts/2026/08/07/apple-aapl-q3-2026-earnings-call-transcript/</loc></url>
<url><loc>https://www.fool.com/earnings/call-transcripts/2026/08/03/abbvie-abbv-q2-2026-earnings-call-transcript/</loc></url>
<url><loc>https://www.fool.com/investing/2026/08/05/some-unrelated-article/</loc></url>
</urlset>
"""


def test_sitemap_yields_ticker_date_and_url():
    entries = MotleyFoolTranscriptScraper._parse_sitemap(_SITEMAP)

    by_ticker = {entry["ticker"]: entry for entry in entries}
    assert set(by_ticker) == {"AAPL", "ABBV"}
    assert by_ticker["AAPL"]["published_at"] == datetime(2026, 8, 7, tzinfo=timezone.utc)
    assert by_ticker["AAPL"]["quarter"] == "Q3 2026"


def test_non_transcript_urls_are_ignored():
    entries = MotleyFoolTranscriptScraper._parse_sitemap(_SITEMAP)

    assert all("call-transcripts" in entry["url"] for entry in entries)


def test_month_scan_walks_backwards_across_a_year_boundary():
    scraper = MotleyFoolTranscriptScraper(lookback_months=3)
    months = scraper._months_to_scan()

    assert len(months) == 3
    # Consecutive, strictly descending, and never month 0.
    assert all(1 <= month <= 12 for _, month in months)


# ---- transcript extraction ---------------------------------------------

_BODY = "Management commentary and Q&A. " * 200

_PAGE = f"""
<html><body>
  <nav>Site navigation that is not transcript</nav>
  <div class="transcript-content">
    <div class="article-body-promobox">Subscribe to our premium service!</div>
    <p>{_BODY}</p>
  </div>
  <aside>Related articles</aside>
</body></html>
"""


def test_extraction_is_scoped_to_the_transcript_container():
    """Everything around the transcript is navigation and promos; feeding it
    to the model would present it as something management said."""
    text = MotleyFoolTranscriptScraper._extract_transcript(_PAGE)

    assert text is not None
    assert "Management commentary" in text
    assert "Site navigation" not in text
    assert "Related articles" not in text
    assert "Subscribe to our premium" not in text


def test_stub_pages_are_rejected():
    stub = '<html><body><div class="transcript-content">Coming soon.</div></body></html>'

    assert MotleyFoolTranscriptScraper._extract_transcript(stub) is None


def test_page_without_a_transcript_container_is_rejected():
    assert MotleyFoolTranscriptScraper._extract_transcript("<html><body><p>x</p></body></html>") is None


def test_transcript_index_is_built_once_under_concurrency():
    """Concurrent ingestion made every worker find the cache empty at the same
    moment and fetch the whole sitemap set itself: eight months times eight
    workers, sixty-four requests to build one index, which became the slowest
    part of a run because those requests are also correctly rate limited.
    """
    import threading

    from app.ingestion.scrapers.earnings_transcript_motley_fool import (
        MotleyFoolTranscriptScraper,
    )

    scraper = MotleyFoolTranscriptScraper(lookback_months=3)
    fetched: list[str] = []
    gate = threading.Barrier(6, timeout=5)

    def fake_fetch(url: str):
        fetched.append(url)
        return None

    scraper.fetch_html = fake_fetch

    def worker():
        gate.wait()
        scraper._transcript_index()

    threads = [threading.Thread(target=worker) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # Three months scanned, once, no matter how many workers asked at once.
    assert len(fetched) == 3
