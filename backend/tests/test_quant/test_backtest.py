"""The backtest's failure modes all inflate the result.

Nothing here can be caught by reading the output. Pooling company-months
shrinks the standard error by roughly an order of magnitude; overlapping
holding periods count every return several times; a long leg measured against
a benchmark inherits the universe's survivorship. Each produces a larger
t-statistic and a more exciting report, which is exactly why each gets a test.
"""

from datetime import date, timedelta

import pytest

from app.engine.quant.backtest import (
    MIN_LEG,
    FactorResult,
    MonthResult,
    _measure,
    _t_of_mean,
    rebalance_dates,
)


def _month(spread_from: tuple[float, float], as_of=date(2026, 1, 1)) -> MonthResult:
    long_return, short_return = spread_from
    return MonthResult(
        as_of=as_of, long_return=long_return, short_return=short_return,
        universe_return=0.0, benchmark_return=0.0,
        long_names=10, short_names=10, information_coefficient=None,
    )


# ---- the sample the t-statistic is entitled to ------------------------


def test_the_t_statistic_counts_rebalances_not_company_months():
    """Fama-MacBeth. Every company in one month shares that month's market, so
    seven thousand company-months are closer to sixty observations than to
    seven thousand. Pooling them shrinks the standard error by roughly an
    order of magnitude and turns noise into a discovery."""
    result = FactorResult(key="x", label="X")
    result.months = [_month((0.02, 0.01)) for _ in range(12)]

    # Twelve rebalances, not twelve times however many companies were in them.
    assert len(result.spreads) == 12
    assert result.mean_spread == pytest.approx(0.01)


def test_no_t_statistic_is_reported_from_too_few_periods():
    """A mean of three monthly spreads has a t-statistic the arithmetic will
    happily produce and the sample cannot support."""
    result = FactorResult(key="x", label="X")
    result.months = [_month((0.05, 0.0)) for _ in range(3)]
    assert result.t_statistic is None


def test_a_constant_spread_has_no_t_statistic_rather_than_an_infinite_one():
    values = [0.01] * 20
    assert _t_of_mean(values) is None


def test_the_hit_rate_exposes_a_mean_carried_by_one_month():
    """A mean spread can be produced entirely by a single period. The hit rate
    is what makes that visible."""
    result = FactorResult(key="x", label="X")
    result.months = [_month((0.0, 0.01)) for _ in range(11)] + [_month((1.0, 0.0))]

    assert result.mean_spread > 0, "the mean is positive"
    assert result.hit_rate == pytest.approx(1 / 12), "but it won in one month of twelve"


# ---- non-overlapping observations -------------------------------------


def test_rebalance_dates_do_not_overlap_their_holding_periods():
    """Rebalancing monthly while holding three months triple-counts every
    return, which is pooling wearing different clothes."""
    dates = rebalance_dates(date(2024, 1, 1), date(2024, 12, 31), step_days=30)
    gaps = {(b - a).days for a, b in zip(dates, dates[1:])}

    assert gaps == {30}, "each holding period must end where the next begins"


def test_the_final_date_is_dropped_because_it_has_no_forward_window():
    dates = rebalance_dates(date(2024, 1, 1), date(2024, 3, 1), step_days=30)
    assert dates[-1] + timedelta(days=30) <= date(2024, 3, 1)


def test_a_window_too_short_for_one_rebalance_yields_nothing():
    assert rebalance_dates(date(2024, 1, 1), date(2024, 1, 10), step_days=30) == []


# ---- portfolio formation ----------------------------------------------


def _spread_inputs(n: int, *, aligned: bool):
    """n companies whose forward returns either follow the factor or oppose it."""
    percentiles = {f"T{i}": i / (n - 1) for i in range(n)}
    forward = {
        f"T{i}": (i / (n - 1)) if aligned else -(i / (n - 1))
        for i in range(n)
    }
    return percentiles, forward


def test_a_factor_that_ranked_returns_shows_a_positive_spread():
    percentiles, forward = _spread_inputs(50, aligned=True)
    month = _measure(
        as_of=date(2026, 1, 1), percentiles=percentiles, forward=forward,
        universe_return=0.0, benchmark_return=0.0, quantiles=5,
    )
    assert month is not None
    assert month.spread > 0
    assert month.information_coefficient is not None and month.information_coefficient > 0.9


def test_a_factor_that_ranked_them_backwards_shows_a_negative_spread():
    percentiles, forward = _spread_inputs(50, aligned=False)
    month = _measure(
        as_of=date(2026, 1, 1), percentiles=percentiles, forward=forward,
        universe_return=0.0, benchmark_return=0.0, quantiles=5,
    )
    assert month is not None
    assert month.spread < 0


def test_a_universe_too_small_to_fill_both_legs_is_skipped():
    """A "top quintile" of four companies is not a portfolio; its return is
    whichever one name moved most."""
    n = MIN_LEG * 5 - 1
    percentiles, forward = _spread_inputs(n, aligned=True)
    month = _measure(
        as_of=date(2026, 1, 1), percentiles=percentiles, forward=forward,
        universe_return=0.0, benchmark_return=0.0, quantiles=5,
    )
    assert month is None


def test_both_legs_are_the_same_size():
    percentiles, forward = _spread_inputs(53, aligned=True)
    month = _measure(
        as_of=date(2026, 1, 1), percentiles=percentiles, forward=forward,
        universe_return=0.0, benchmark_return=0.0, quantiles=5,
    )
    assert month is not None
    assert month.long_names == month.short_names


# ---- survivorship ------------------------------------------------------


def test_the_spread_is_free_of_the_benchmark_and_the_long_leg_is_not():
    """The point of leading with the spread. Both legs are drawn from the same
    survivors, so the bias mostly cancels; a long leg measured against a
    benchmark inherits all of it."""
    month = MonthResult(
        as_of=date(2026, 1, 1), long_return=0.05, short_return=0.01,
        universe_return=0.04, benchmark_return=0.03,
        long_names=10, short_names=10, information_coefficient=None,
    )
    assert month.spread == pytest.approx(0.04)
    assert month.long_excess == pytest.approx(0.02)


def test_long_excess_is_absent_without_a_benchmark_rather_than_zero():
    month = MonthResult(
        as_of=date(2026, 1, 1), long_return=0.05, short_return=0.01,
        universe_return=0.04, benchmark_return=None,
        long_names=10, short_names=10, information_coefficient=None,
    )
    assert month.long_excess is None


def test_the_survivorship_free_lunch_is_reported_as_a_number():
    """Whatever the universe earned over the benchmark is the size of the gift
    its construction handed every long-only figure in the report."""
    from app.engine.quant.backtest import SurvivorshipReport

    report = SurvivorshipReport(
        months=60, mean_universe_return=0.014, mean_benchmark_return=0.009,
        companies=128, late_entrants=3,
    )
    assert report.excess == pytest.approx(0.005)


def test_the_free_lunch_is_unknowable_without_a_benchmark():
    from app.engine.quant.backtest import SurvivorshipReport

    report = SurvivorshipReport(
        months=60, mean_universe_return=0.014, mean_benchmark_return=None,
        companies=128, late_entrants=3,
    )
    assert report.excess is None
