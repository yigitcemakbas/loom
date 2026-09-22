"""Point-in-time access is the load-bearing guarantee of the quant layer.

Every factor is arithmetic anyone can check. What cannot be checked by reading
the output is whether the inputs were knowable at the time, and a factor built
from data the market did not have is not a factor, it is a memory. It also
fails silently and flatters everything downstream, which is why these tests
come before the ones about the arithmetic.
"""

from datetime import date

from app.engine.quant.series import INSTANT, YEAR, FactSeries, Observation


def _obs(metric, period, end, filed, value) -> Observation:
    return Observation(
        metric=metric, period=period,
        period_end=date.fromisoformat(end), filed_on=date.fromisoformat(filed),
        value=value,
    )


def test_a_figure_is_invisible_before_it_was_filed():
    """A December quarter that reaches EDGAR in February is unknowable in
    January. Ordering by period end would say otherwise."""
    series = FactSeries([_obs("revenue", YEAR, "2025-12-31", "2026-02-15", 100.0)])

    assert series.as_of(date(2026, 1, 31)).latest("revenue", YEAR) is None
    assert series.as_of(date(2026, 2, 28)).latest("revenue", YEAR) is not None


def test_a_restatement_does_not_rewrite_what_was_known_at_the_time():
    """Two filings of one period. Asking before the correction must return the
    number the market actually traded on."""
    original = _obs("net_income", YEAR, "2025-12-31", "2026-02-10", 500.0)
    restated = _obs("net_income", YEAR, "2025-12-31", "2026-08-01", 300.0)
    series = FactSeries([original, restated])

    assert series.as_of(date(2026, 3, 1)).value("net_income", YEAR) == 500.0
    assert series.as_of(date(2026, 9, 1)).value("net_income", YEAR) == 300.0


def test_the_newest_version_wins_once_both_are_visible():
    series = FactSeries([
        _obs("assets", INSTANT, "2025-12-31", "2026-02-10", 1000.0),
        _obs("assets", INSTANT, "2025-12-31", "2026-08-01", 900.0),
    ])
    assert series.value("assets", INSTANT) == 900.0
    # And the corrected period is still one period, not two.
    assert len(series.history("assets", INSTANT)) == 1


def test_prior_counts_reported_periods_not_calendar_days():
    """Filers skip periods and change fiscal calendars. Subtracting 365 days
    pairs a year against eighteen months when they do."""
    series = FactSeries([
        _obs("revenue", YEAR, "2022-12-31", "2023-02-01", 10.0),
        _obs("revenue", YEAR, "2024-06-30", "2024-08-01", 20.0),   # calendar gap
        _obs("revenue", YEAR, "2025-06-30", "2025-08-01", 30.0),
    ])
    pair = series.pair("revenue", YEAR)
    assert pair is not None
    current, previous = pair
    assert (current.value, previous.value) == (30.0, 20.0)


def test_metrics_and_periods_do_not_bleed_into_each_other():
    """A quarter paired against a year is the classic way to produce a ratio
    that means nothing."""
    series = FactSeries([
        _obs("revenue", "quarter", "2025-12-31", "2026-02-01", 25.0),
        _obs("revenue", YEAR, "2025-12-31", "2026-02-01", 100.0),
    ])
    assert series.value("revenue", YEAR) == 100.0
    assert series.value("revenue", "quarter") == 25.0
    assert series.value("net_income", YEAR) is None


def test_an_empty_series_answers_rather_than_raising():
    series = FactSeries([])
    assert series.latest("revenue", YEAR) is None
    assert series.pair("revenue", YEAR) is None
    assert series.newest_filing is None
