"""FINRA short interest adapter tests.

No network. What matters here is the parsing of a CSV response that arrives
from a JSON-request endpoint, and the tolerance for the fields FINRA leaves
blank on any given row.

The date-bounding behaviour is pinned because the first version of this adapter
requested rows by count alone. FINRA returns oldest-first and cannot be sorted
without an EQUAL filter on the partition key, so a plain limit silently
truncated the newest settlement dates: NVDA reported a "latest" reading four
months stale, which is exactly the number the spike rule depends on.
"""

from datetime import datetime, timezone

from app.ingestion.facts.finra_short_interest import FinraShortInterestAdapter

_HEADER = (
    "accountingYearMonthNumber,symbolCode,issueName,issuerServicesGroupExchangeCode,"
    "marketClassCode,currentShortPositionQuantity,previousShortPositionQuantity,"
    "stockSplitFlag,averageDailyVolumeQuantity,daysToCoverQuantity,revisionFlag,"
    "changePercent,changePreviousNumber,settlementDate"
)


def _row(**over) -> dict:
    base = {
        "symbolCode": "NVDA",
        "currentShortPositionQuantity": "285956804",
        "previousShortPositionQuantity": "292667375",
        "averageDailyVolumeQuantity": "113000000",
        "daysToCoverQuantity": "2.52",
        "changePercent": "-2.29",
        "changePreviousNumber": "-6710571",
        "stockSplitFlag": "",
        "marketClassCode": "NASDAQ",
        "settlementDate": "2026-08-14",
    }
    base.update(over)
    return base


def test_row_becomes_a_short_interest_fact():
    dto = FinraShortInterestAdapter._to_fact("NVDA", _row())

    assert dto is not None
    assert dto.fact_type == "short_interest"
    assert dto.unit == "shares"
    assert dto.value == 285956804
    assert dto.as_of_date == datetime(2026, 8, 14, tzinfo=timezone.utc)


def test_supporting_figures_are_carried_through():
    """The spike rule reads `value`, but a reader wants days-to-cover and the
    published change alongside it."""
    dto = FinraShortInterestAdapter._to_fact("NVDA", _row())

    assert dto is not None
    assert dto.attributes["days_to_cover"] == 2.52
    assert dto.attributes["change_percent"] == -2.29
    assert dto.attributes["previous_short_position"] == 292667375


def test_rows_without_a_position_or_date_are_dropped():
    assert FinraShortInterestAdapter._to_fact("NVDA", _row(currentShortPositionQuantity="")) is None
    assert FinraShortInterestAdapter._to_fact("NVDA", _row(settlementDate="")) is None


def test_unparseable_numbers_do_not_raise():
    """FINRA leaves fields blank rather than zero, and occasionally emits a
    value that is not a number. One bad column must not lose the row."""
    dto = FinraShortInterestAdapter._to_fact(
        "NVDA", _row(daysToCoverQuantity="", changePercent="n/a", averageDailyVolumeQuantity=None)
    )

    assert dto is not None
    assert dto.value == 285956804
    assert dto.attributes["days_to_cover"] is None
    assert dto.attributes["change_percent"] is None


def test_malformed_date_drops_the_row():
    assert FinraShortInterestAdapter._to_fact("NVDA", _row(settlementDate="14/08/2026")) is None


def test_facts_are_returned_oldest_first(monkeypatch):
    """The spike rule compares the last two entries, so ordering is not
    cosmetic: reversed input would make it compare the wrong pair."""
    adapter = FinraShortInterestAdapter()
    monkeypatch.setattr(
        adapter,
        "_fetch_rows",
        lambda ticker: [
            _row(settlementDate="2026-08-14", currentShortPositionQuantity="300"),
            _row(settlementDate="2026-06-30", currentShortPositionQuantity="100"),
            _row(settlementDate="2026-07-31", currentShortPositionQuantity="200"),
        ],
    )

    dates = [f.as_of_date.date().isoformat() for f in adapter.fetch("NVDA")]

    assert dates == ["2026-06-30", "2026-07-31", "2026-08-14"]


def test_request_is_bounded_by_date_not_row_count(monkeypatch):
    """Regression: requesting by row count alone truncated the newest readings,
    because FINRA returns oldest-first and refuses to sort without an EQUAL
    filter on the partition key."""
    adapter = FinraShortInterestAdapter(lookback_days=365)
    captured: dict = {}

    class _Resp:
        status_code = 200
        text = _HEADER  # header only, no data rows

    def _post(url, json):
        captured.update(json)
        return _Resp()

    monkeypatch.setattr(adapter._client, "post", _post)
    adapter.fetch("NVDA")

    fields = {f["fieldName"]: f for f in captured["compareFilters"]}
    assert fields["symbolCode"]["compareType"] == "EQUAL"
    assert fields["settlementDate"]["compareType"] == "GTE", "must bound by date, not count alone"


def test_json_error_body_is_not_parsed_as_csv(monkeypatch):
    """The endpoint returns a JSON error even when text/plain was requested.
    Feeding that to the CSV reader would produce nonsense rows."""
    adapter = FinraShortInterestAdapter()

    class _Resp:
        status_code = 200
        text = '{"statusCode":400,"message":"Bad Request"}'

    monkeypatch.setattr(adapter._client, "post", lambda url, json: _Resp())

    assert adapter.fetch("NVDA") == []


def test_non_200_returns_nothing(monkeypatch):
    """An unavailable optional source degrades to no data, never an exception
    that would abort the whole ingestion run."""
    adapter = FinraShortInterestAdapter()

    class _Resp:
        status_code = 503
        text = ""

    monkeypatch.setattr(adapter._client, "post", lambda url, json: _Resp())

    assert adapter.fetch("NVDA") == []


def test_transport_failure_returns_nothing(monkeypatch):
    adapter = FinraShortInterestAdapter()

    def _boom(url, json):
        raise OSError("connection reset")

    monkeypatch.setattr(adapter._client, "post", _boom)

    assert adapter.fetch("NVDA") == []
