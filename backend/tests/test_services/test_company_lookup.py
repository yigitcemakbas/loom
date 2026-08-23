"""Regression test for the "add any ticker" fix: CompanyLookupService is
what lets the watchlist accept a ticker it has never seen before, instead
of requiring every company to be hand-seeded first. No live network call:
httpx.Client.get is monkeypatched with a small fixture matching SEC's
actual company_tickers.json shape.
"""

from app.services.company_lookup import CompanyLookupService

_FIXTURE = {
    "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "1": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
}


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


def test_lookup_resolves_known_ticker(monkeypatch):
    service = CompanyLookupService()
    monkeypatch.setattr(service._client, "get", lambda url: _FakeResponse(_FIXTURE))

    info = service.lookup("nvda")  # lowercase, as a user might type it

    assert info is not None
    assert info.ticker == "NVDA"
    assert info.name == "NVIDIA CORP"
    assert info.cik == "0001045810"


def test_lookup_returns_none_for_unknown_ticker(monkeypatch):
    service = CompanyLookupService()
    monkeypatch.setattr(service._client, "get", lambda url: _FakeResponse(_FIXTURE))

    assert service.lookup("ZZZZ") is None


def test_lookup_only_fetches_once(monkeypatch):
    service = CompanyLookupService()
    call_count = 0

    def fake_get(url):
        nonlocal call_count
        call_count += 1
        return _FakeResponse(_FIXTURE)

    monkeypatch.setattr(service._client, "get", fake_get)

    service.lookup("AAPL")
    service.lookup("NVDA")

    assert call_count == 1
