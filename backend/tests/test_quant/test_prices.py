"""Price history, where a single off-by-one is indistinguishable from an edge.

Two mistakes are possible here and both produce plausible numbers. Returning
Monday's close when asked for Saturday's is tomorrow's information wearing
today's date, and it would flatter every valuation and every event study by
exactly the move it was supposed to predict. Measuring a return from raw closes
reads a two-for-one split as a fifty percent crash. Neither would look wrong in
any output.
"""

from datetime import date, timedelta

import pytest

from app.engine.quant.prices import Bar, PriceHistory


def _weekdays(start: date, count: int, price: float = 100.0, step: float = 0.0):
    """A run of sessions, weekends skipped, so the fixtures look like a real
    calendar rather than a continuous one."""
    bars = []
    current = start
    made = 0
    while made < count:
        if current.weekday() < 5:
            value = price + step * made
            bars.append(Bar(session_date=current, close=value, adjusted_close=value))
            made += 1
        current += timedelta(days=1)
    return bars


def test_a_weekend_lookup_returns_fridays_close_not_mondays():
    """The single most dangerous case. A nearest-match returns Monday, which is
    tomorrow's information, and it would flatter every valuation by the move it
    was meant to predict."""
    friday = date(2026, 3, 6)
    monday = date(2026, 3, 9)
    history = PriceHistory([
        Bar(session_date=friday, close=100.0, adjusted_close=100.0),
        Bar(session_date=monday, close=140.0, adjusted_close=140.0),
    ])

    saturday = date(2026, 3, 7)
    sunday = date(2026, 3, 8)
    assert history.close_on(saturday) == 100.0
    assert history.close_on(sunday) == 100.0
    assert history.close_on(monday) == 140.0


def test_a_date_before_the_series_begins_has_no_answer():
    history = PriceHistory(_weekdays(date(2026, 3, 2), 5))
    assert history.close_on(date(2026, 1, 1)) is None


def test_a_hole_in_the_series_is_not_filled_from_a_fortnight_ago():
    """Beyond the lookback the series has a genuine gap, and saying so is
    better than answering with a stale price."""
    history = PriceHistory([Bar(session_date=date(2026, 1, 5), close=100.0, adjusted_close=100.0)])
    assert history.close_on(date(2026, 1, 9)) == 100.0     # within the window
    assert history.close_on(date(2026, 2, 20)) is None     # far beyond it


def test_returns_use_adjusted_closes_so_a_split_is_not_a_crash():
    """A two-for-one split halves the raw close. Measured raw, the share
    appears to have lost half its value on the day."""
    before = date(2026, 2, 2)
    after = date(2026, 6, 1)
    history = PriceHistory([
        # Raw close halves across the split; the adjusted series does not.
        Bar(session_date=before, close=200.0, adjusted_close=100.0),
        Bar(session_date=after, close=110.0, adjusted_close=110.0),
    ])

    assert history.total_return(before, after) == pytest.approx(0.10)


def test_market_cap_uses_the_raw_close_not_the_adjusted_one():
    """The other half of the same distinction: a capitalisation built from
    adjusted closes is wrong by every split the company ever did."""
    session = date(2026, 3, 2)
    history = PriceHistory([Bar(session_date=session, close=200.0, adjusted_close=100.0)])
    assert history.close_on(session) == 200.0


def test_a_return_needs_two_distinct_sessions():
    session = date(2026, 3, 2)
    history = PriceHistory([Bar(session_date=session, close=100.0, adjusted_close=100.0)])
    assert history.total_return(session, session) is None


def test_coverage_is_reported_rather_than_assumed():
    """A momentum factor computed from four months of history is a different
    measurement, not a weak one, and would be ranked beside full windows as
    though it were the same thing."""
    history = PriceHistory(_weekdays(date(2026, 1, 5), 60))
    as_of = date(2026, 3, 27)

    assert history.covers(as_of, back_days=30) is True
    assert history.covers(as_of, back_days=400) is False


def test_daily_returns_stay_inside_the_window():
    history = PriceHistory(_weekdays(date(2026, 1, 5), 40, price=100.0, step=1.0))
    inside = history.daily_returns(date(2026, 1, 5), date(2026, 1, 16))
    everything = history.daily_returns(date(2026, 1, 1), date(2026, 12, 31))

    assert len(inside) < len(everything)
    assert all(r > 0 for r in inside), "a rising fixture must produce positive returns"


def test_an_empty_history_answers_rather_than_raising():
    history = PriceHistory([])
    assert history.close_on(date(2026, 3, 2)) is None
    assert history.total_return(date(2026, 1, 1), date(2026, 3, 2)) is None
    assert history.daily_returns(date(2026, 1, 1), date(2026, 3, 2)) == []
    assert history.covers(date(2026, 3, 2), back_days=30) is False
    assert history.newest is None
