"""Tests for the dashboard tape's pure logic.

The database-backed assembly is exercised through the route; what is worth
pinning here is the part that decides *what may be claimed*, since the module's
whole premise is that it reports what has already happened and never predicts.
"""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.engine import tape
from app.engine.tape import TapeItem, _countdown, _last_report_move, _money, _news_items


def _event(as_of: date, *, reported: bool, surprise: float | None = None):
    return SimpleNamespace(
        as_of_date=as_of,
        attributes={"reported": reported, "eps_surprise_percent": surprise},
    )


def test_countdown_names_the_urgent_days():
    assert _countdown(0) == "TODAY"
    assert _countdown(1) == "TOMORROW"
    assert _countdown(9) == "9D"


@pytest.mark.parametrize(
    "value,expected",
    [(52_101_559_912, "$52.1B"), (940_000_000, "$940M"), (1234, "$1,234"), (None, None)],
)
def test_money_formats_at_readable_scale(value, expected):
    assert _money(value) == expected


def test_no_move_claimed_when_nothing_has_been_reported(monkeypatch):
    """A company with only future earnings dates has no realised move, and the
    tape must say nothing rather than reach for a placeholder."""
    monkeypatch.setattr(
        tape, "get_price_source", lambda: pytest.fail("prices must not be fetched")
    )
    events = [_event(date(2026, 11, 17), reported=False)]
    assert _last_report_move("NVDA", events) is None


def test_move_uses_the_most_recent_reported_quarter(monkeypatch):
    """Deliberately the last report and not an average: with one or two stored
    quarters an average is a single observation wearing a statistic's clothes."""
    seen = {}

    def fake_reaction(series, when, *, sessions):
        seen["when"] = when
        seen["sessions"] = sessions
        return SimpleNamespace(change_percent=-4.2, sessions=2, already_moved=True)

    monkeypatch.setattr(tape, "get_price_source", lambda: SimpleNamespace(get=lambda t, r: object()))
    monkeypatch.setattr(tape, "reaction_since", fake_reaction)

    events = [
        _event(date(2026, 5, 28), reported=True, surprise=1.0),
        _event(date(2026, 8, 26), reported=True, surprise=3.82),
        _event(date(2026, 11, 17), reported=False),
    ]
    note = _last_report_move("NVDA", events)

    assert seen["when"].date() == date(2026, 8, 26)
    # An earnings move lands on the next open; ten sessions would blend it into
    # a fortnight of unrelated drift.
    assert seen["sessions"] == tape.EARNINGS_REACTION_SESSIONS
    assert "26 Aug" in note
    assert "fell 4.2%" in note
    assert "+3.8% EPS surprise" in note


def test_move_is_omitted_when_prices_are_unavailable(monkeypatch):
    """A missing price series costs the move note and nothing else: the rest of
    the earnings item is still worth showing."""

    def boom():
        raise RuntimeError("provider down")

    monkeypatch.setattr(tape, "get_price_source", boom)
    events = [_event(date(2026, 8, 26), reported=True, surprise=1.0)]
    assert _last_report_move("NVDA", events) is None


class _Repo:
    def __init__(self, documents, companies):
        self._documents = documents
        self._companies = companies

    # DocumentRepository
    def list_all(self, *, doc_subtype=None, limit=200):
        return self._documents

    # WatchlistRepository
    def get_or_create_default(self):
        return SimpleNamespace(id="wl")

    def list_companies(self, _id):
        return self._companies


def _doc(company_id, title, published_at):
    return SimpleNamespace(
        company_id=company_id,
        title=title,
        published_at=published_at,
        source_url="https://example.com/x",
        source_name="finnhub",
    )


def _install_repos(monkeypatch, documents, companies):
    repo = _Repo(documents, companies)
    monkeypatch.setattr(tape, "DocumentRepository", lambda db: repo)
    monkeypatch.setattr(tape, "WatchlistRepository", lambda db: repo)
    monkeypatch.setattr(tape, "CompanyRepository", lambda db: repo)


def test_news_is_limited_per_ticker(monkeypatch):
    """One company having a noisy week must not crowd the rest of the watchlist
    off a feed whose entire purpose is breadth."""
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    companies = [SimpleNamespace(id="a", ticker="AAPL"), SimpleNamespace(id="b", ticker="MSFT")]
    documents = [_doc("a", f"Apple story {i}", now - timedelta(hours=i)) for i in range(10)]
    documents += [_doc("b", "Microsoft story", now - timedelta(hours=20))]
    _install_repos(monkeypatch, documents, companies)

    items = _news_items(object(), now)
    tickers = [i.ticker for i in items]

    assert tickers.count("AAPL") == tape.MAX_NEWS_PER_TICKER
    assert "MSFT" in tickers


def test_news_excludes_untracked_companies_and_stale_headlines(monkeypatch):
    """The feed is derived from the watchlist on every call, so a company that
    is no longer tracked disappears rather than lingering."""
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    companies = [SimpleNamespace(id="a", ticker="AAPL")]
    documents = [
        _doc("a", "Fresh Apple story", now - timedelta(hours=2)),
        _doc("a", "Ancient Apple story", now - timedelta(days=40)),
        _doc("zzz", "Story about an untracked company", now - timedelta(hours=1)),
    ]
    _install_repos(monkeypatch, documents, companies)

    headlines = [i.headline for i in _news_items(object(), now)]
    assert headlines == ["Fresh Apple story"]


def test_news_item_carries_its_source_link(monkeypatch):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    companies = [SimpleNamespace(id="a", ticker="AAPL")]
    _install_repos(monkeypatch, [_doc("a", "Headline", now)], companies)

    item = _news_items(object(), now)[0]
    assert isinstance(item, TapeItem)
    assert item.kind == "news"
    assert item.href == "https://example.com/x"
    assert item.label == "NEWS"
