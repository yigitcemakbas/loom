"""Finnhub adapter tests. No network: what matters here is the mapping from
one API item to one document, and the filtering that keeps thin items out of
the engine."""

from datetime import datetime, timezone

from app.ingestion.news_api import FinnhubNewsAdapter, _core_name, is_about_company

_SUMMARY = (
    "Apple said component costs rose across memory and advanced semiconductors "
    "during the quarter, and warned the trend is likely to intensify into next year."
)


def _item(**overrides) -> dict:
    base = {
        "headline": "Apple flags rising component costs",
        "summary": _SUMMARY,
        "url": "https://example.com/story",
        "datetime": 1787000000,
        "source": "Reuters",
        "category": "company",
        "id": 12345,
    }
    base.update(overrides)
    return base


def test_item_becomes_a_document():
    dto = FinnhubNewsAdapter._to_document("AAPL", _item())

    assert dto is not None
    assert dto.source_type == "news_api"
    assert dto.doc_subtype == "news"
    assert dto.source_url == "https://example.com/story"
    assert dto.metadata["publisher"] == "Reuters"


def test_headline_is_kept_in_the_body():
    """The claim often lives in the framing, not the body; dropping the
    headline would hand the engine the weaker half of the item."""
    dto = FinnhubNewsAdapter._to_document("AAPL", _item())

    assert dto is not None
    assert dto.raw_text.startswith("Apple flags rising component costs")
    assert _SUMMARY in dto.raw_text


def test_html_entities_are_decoded():
    """Finnhub returns HTML-encoded text. Left encoded it renders as
    "Storage &amp; Peripherals" in the UI and reaches the model as an
    entity rather than a word."""
    dto = FinnhubNewsAdapter._to_document(
        "AAPL",
        _item(headline="Storage &amp; Peripherals rally", summary=f"{_SUMMARY} &quot;quoted&quot;"),
    )

    assert dto is not None
    assert dto.title == "Storage & Peripherals rally"
    assert "&amp;" not in dto.raw_text
    assert '"quoted"' in dto.raw_text


def test_thin_items_are_skipped():
    """A bare headline gives the engine nothing defensible to say, and every
    item it does keep costs a model call."""
    assert FinnhubNewsAdapter._to_document("AAPL", _item(summary="Shares moved.")) is None
    assert FinnhubNewsAdapter._to_document("AAPL", _item(summary="")) is None


def test_items_without_a_headline_are_skipped():
    assert FinnhubNewsAdapter._to_document("AAPL", _item(headline="")) is None


def test_timestamp_is_converted_to_utc():
    dto = FinnhubNewsAdapter._to_document("AAPL", _item(datetime=1787000000))

    assert dto is not None
    assert dto.published_at == datetime.fromtimestamp(1787000000, tz=timezone.utc)


def test_missing_timestamp_is_tolerated():
    dto = FinnhubNewsAdapter._to_document("AAPL", _item(datetime=0))

    assert dto is not None
    assert dto.published_at is None


# ---- relevance gating --------------------------------------------------
#
# Finnhub returns anything that mentions the ticker. Against a live AAPL pull,
# 106 of 176 items never said "Apple" at all and 11 of the 15 most recent were
# about other companies, which is what the engine would otherwise have analysed
# and attributed to Apple. These cases are taken from that real sample.


def test_corporate_suffixes_are_stripped_to_the_headline_form():
    assert _core_name("Apple Inc.") == "apple"
    assert _core_name("Micron Technology, Inc.") == "micron"
    assert _core_name("NVIDIA CORP") == "nvidia"
    assert _core_name("DELTA AIR LINES, INC.") == "delta air lines"


def test_a_name_that_is_all_suffix_falls_back_to_the_full_name():
    """Stripping can leave nothing useful behind; an empty matcher would
    match every headline."""
    assert _core_name("Technology Group") == "technology group"


def test_headlines_naming_the_company_are_kept():
    for headline in [
        "Will Apple's risk appetite change under John Ternus?",
        "Jim Cramer Wants You To Look At The Bigger Picture For Apple Inc. (NASDAQ:AAPL)",
        "Every S&P 500 Index Fund Owner Holds More Nvidia Than Apple",
    ]:
        assert is_about_company(headline, company_name="Apple Inc.", ticker="AAPL"), headline


def test_headlines_about_other_companies_are_rejected():
    for headline in [
        "Samsung Crash Brings Semi Selling; Nvidia Earnings Ahead",
        "Berkshire Hathaway Is A Net Buyer Of Stocks, Finally",
        "10 Information Technology Stocks Whale Activity In Today's Session",
        "Trump's 50% Canada Auto Tariff Shock: These ETFs Could Be in the Crosshairs",
    ]:
        assert not is_about_company(headline, company_name="Apple Inc.", ticker="AAPL"), headline


def test_ticker_match_is_case_sensitive_and_bounded():
    """A short ticker matched loosely would keep everything: 'F' appears inside
    ordinary words, and lowercase 'dal' inside 'scandal'."""
    assert is_about_company("Ford (F) raises guidance", company_name="Ford Motor Co", ticker="F")
    assert not is_about_company(
        "Analysts weigh in for the quarter", company_name="Ford Motor Co", ticker="F"
    )
    assert not is_about_company(
        "A scandal at the airline", company_name="Delta Air Lines, Inc.", ticker="DAL"
    )


def test_empty_headline_is_rejected():
    assert not is_about_company("", company_name="Apple Inc.", ticker="AAPL")


def test_adapter_is_unavailable_without_a_key(monkeypatch):
    """A missing optional source must degrade to nothing, never take down a
    run that SEC EDGAR would otherwise complete."""
    from app.ingestion import news_api

    monkeypatch.setattr(news_api.settings, "finnhub_api_key", "")
    adapter = FinnhubNewsAdapter()

    assert adapter.available is False
    assert adapter.fetch("AAPL") == []
