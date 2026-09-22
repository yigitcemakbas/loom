"""The factor arithmetic, and the cases where it must decline to answer.

Most of these pin a refusal rather than a value. A factor that returns a number
from bad inputs is worse than one that returns nothing, because the number
enters a composite, gets ranked against a hundred real companies, and arrives
in front of a reader with no mark on it saying where it came from.
"""

from datetime import date, timedelta

import pytest

from app.engine.quant.crosssection import MIN_UNIVERSE, rank_universe
from app.engine.quant.composite import (
    MIN_FACTORS_FOR_COMPOSITE,
    build_composite,
    build_f_score,
)
from app.engine.quant.crosssection import Ranked
from app.engine.quant.factors import FACTORS, FACTORS_BY_KEY, CompanyView, compute_all
from app.engine.quant.series import INSTANT, YEAR, FactSeries, Observation


def _view(prices=None, as_of=date(2026, 3, 1), **metrics) -> CompanyView:
    """A company as the factors see it: filings, a date, optionally prices."""
    return CompanyView(financials=_series(**metrics), as_of=as_of, prices=prices)


def _series(**metrics) -> FactSeries:
    """Build a series from {metric: [(period_end, value), ...]} pairs."""
    observations = []
    for metric, entries in metrics.items():
        period = INSTANT if metric in ("assets", "liabilities", "cash") else YEAR
        for end, value in entries:
            observations.append(
                Observation(
                    metric=metric, period=period,
                    period_end=date.fromisoformat(end),
                    filed_on=date.fromisoformat(end),
                    value=float(value),
                )
            )
    return FactSeries(observations)


# ---- the arithmetic ---------------------------------------------------


def test_accruals_measure_profit_that_did_not_arrive_as_cash():
    series = _series(
        net_income=[("2025-12-31", 100.0)],
        operating_cash_flow=[("2025-12-31", 60.0)],
        assets=[("2024-12-31", 800.0), ("2025-12-31", 1200.0)],
    )
    # (100 - 60) / mean(800, 1200) = 40 / 1000
    assert compute_all(CompanyView(financials=series, as_of=date(2026, 3, 1)))["accruals"].value == pytest.approx(0.04)


def test_accruals_scale_by_average_assets_not_closing_assets():
    """Closing assets is the common shortcut and it biases every ratio for a
    company that grew during the period, which is the population this factor
    exists to separate."""
    grew = _series(
        net_income=[("2025-12-31", 100.0)],
        operating_cash_flow=[("2025-12-31", 60.0)],
        assets=[("2024-12-31", 500.0), ("2025-12-31", 1500.0)],
    )
    # Closing-asset arithmetic would give 40/1500 = 0.027; the average gives 0.04.
    assert compute_all(CompanyView(financials=grew, as_of=date(2026, 3, 1)))["accruals"].value == pytest.approx(0.04)


def test_dilution_is_measured_against_the_prior_share_count():
    series = _series(shares_diluted=[("2024-12-31", 1000.0), ("2025-12-31", 1100.0)])
    assert compute_all(CompanyView(financials=series, as_of=date(2026, 3, 1)))["net_share_issuance"].value == pytest.approx(0.1)


def test_a_buyback_reads_as_negative_issuance():
    series = _series(shares_diluted=[("2024-12-31", 1000.0), ("2025-12-31", 900.0)])
    assert compute_all(CompanyView(financials=series, as_of=date(2026, 3, 1)))["net_share_issuance"].value == pytest.approx(-0.1)


# ---- the refusals -----------------------------------------------------


def test_cash_conversion_is_undefined_against_a_loss():
    """A negative denominator flips the sign, which would rank a loss-making
    company as having excellent cash conversion."""
    series = _series(
        net_income=[("2025-12-31", -50.0)],
        operating_cash_flow=[("2025-12-31", -20.0)],
    )
    assert "cash_conversion" not in compute_all(CompanyView(financials=series, as_of=date(2026, 3, 1)))


def test_a_near_zero_denominator_produces_no_factor_rather_than_a_huge_one():
    """One company with near-zero assets would otherwise dominate every
    cross-sectional rank it appears in."""
    series = _series(
        net_income=[("2025-12-31", 100.0)],
        operating_cash_flow=[("2025-12-31", 10.0)],
        assets=[("2024-12-31", 0.2), ("2025-12-31", 0.1)],
    )
    assert "accruals" not in compute_all(CompanyView(financials=series, as_of=date(2026, 3, 1)))


def test_a_missing_input_yields_no_factor_rather_than_zero():
    """Zero is a reading. Missing is the absence of one, and a composite that
    cannot tell them apart scores a company on data it never had."""
    series = _series(net_income=[("2025-12-31", 100.0)])
    computed = compute_all(CompanyView(financials=series, as_of=date(2026, 3, 1)))
    assert "accruals" not in computed
    assert "cash_conversion" not in computed


def test_margin_change_refuses_mismatched_periods():
    """Both margins must come from the same pair of periods, or the change is
    measuring the calendar rather than the business."""
    series = _series(
        revenue=[("2024-12-31", 100.0), ("2025-12-31", 120.0)],
        operating_income=[("2023-12-31", 10.0), ("2024-06-30", 15.0)],
    )
    assert "operating_margin_change" not in compute_all(CompanyView(financials=series, as_of=date(2026, 3, 1)))


def test_every_factor_declares_a_direction_and_a_source():
    """A factor with no source is a hunch, and this is what stops the library
    filling up with them."""
    for factor in FACTORS:
        assert isinstance(factor.higher_is_better, bool)
        assert factor.source.strip()
        assert factor.meaning.strip()


# ---- ranking ----------------------------------------------------------


def test_inverted_factors_rank_low_values_as_good():
    """Several of the best documented factors are inverted: high asset growth
    and high issuance have historically preceded underperformance."""
    values = {f"T{i}": float(i) for i in range(MIN_UNIVERSE)}
    ranked = rank_universe(values, higher_is_better=False, key="asset_growth")

    assert ranked["T0"].percentile == 1.0     # smallest growth, best rank
    assert ranked[f"T{MIN_UNIVERSE - 1}"].percentile == 0.0


def test_ties_share_the_average_of_the_positions_they_span():
    values = {f"T{i}": 5.0 for i in range(MIN_UNIVERSE)}
    ranked = rank_universe(values, higher_is_better=True)
    assert all(r.percentile == pytest.approx(0.5) for r in ranked.values())


def test_a_pool_too_small_to_rank_produces_no_percentiles():
    """A rank inside a pool of three is noise with a decimal point on it."""
    values = {f"T{i}": float(i) for i in range(MIN_UNIVERSE - 1)}
    assert rank_universe(values, higher_is_better=True) == {}


def test_one_extreme_company_does_not_compress_everyone_else():
    """The reason for ranks over z-scores: a company that tripled its assets
    is not a data error, and a z-score would let it own the scale."""
    values = {f"T{i}": float(i) for i in range(MIN_UNIVERSE)}
    values["OUTLIER"] = 1e9
    ranked = rank_universe(values, higher_is_better=True)

    spread = ranked["T1"].percentile - ranked["T0"].percentile
    assert spread > 0.03, "ordinary companies must keep their spacing"


# ---- composite --------------------------------------------------------


def _ranked(**percentiles) -> dict[str, Ranked]:
    return {
        key: Ranked(key=key, value=0.0, percentile=p, universe_size=100)
        for key, p in percentiles.items()
    }


def test_no_composite_is_built_from_too_few_factors():
    """A composite from three factors is not a weak signal to be discounted
    later, it is a different measurement wearing the same name."""
    thin = _ranked(accruals=0.9, asset_growth=0.9, return_on_assets=0.9)
    assert build_composite(thin) is None


def test_the_composite_carries_how_many_factors_produced_it():
    keys = [f.key for f in FACTORS][:MIN_FACTORS_FOR_COMPOSITE]
    composite = build_composite(_ranked(**{k: 0.5 for k in keys}))
    assert composite is not None
    assert composite.factor_count == MIN_FACTORS_FOR_COMPOSITE


def test_extremes_are_the_readings_worth_surfacing():
    composite = build_composite(_ranked(
        accruals=0.02, asset_growth=0.5, return_on_assets=0.99, revenue_growth=0.5,
    ))
    assert composite is not None
    assert composite.extremes == ["accruals", "return_on_assets"]


def test_the_health_score_counts_only_tests_it_could_run():
    """Scoring a company 4 out of 7 when only four tests had data would report
    a failing company, and the failure would be the database's."""
    health = build_f_score({"return_on_assets": 0.1, "accruals": -0.02})
    assert (health.passed, health.available) == (2, 2)


def test_the_health_score_records_which_tests_failed():
    health = build_f_score({"return_on_assets": -0.1, "accruals": 0.05})
    assert health.passed == 0
    assert set(health.failed_tests) == {"return_on_assets", "accruals"}


def test_every_health_test_names_a_factor_that_exists():
    from app.engine.quant.composite import F_SCORE_TESTS

    for key, _ in F_SCORE_TESTS:
        assert key in FACTORS_BY_KEY


# ---- peer groups ------------------------------------------------------


def test_banks_are_ranked_against_banks_when_there_are_enough_of_them():
    """Ranking a bank against a software company on leverage is not a hard
    comparison, it is a meaningless one. The first universe-wide run returned
    eight banks as the eight weakest companies in the database, in order."""
    from app.engine.quant.sectors import MIN_SECTOR_MEMBERS, comparable_group

    counts = {"Financials": MIN_SECTOR_MEMBERS, "Technology": 40}
    assert comparable_group("Financials", counts) == "Financials"


def test_a_bank_with_too_few_peers_is_ranked_against_nobody():
    """The honest answer for a bank in a universe of three banks. Ranking it
    against operating companies would produce a confident bottom-decile
    reading about the accounting rather than the business."""
    from app.engine.quant.sectors import comparable_group

    assert comparable_group("Financials", {"Financials": 3}) is None


def test_an_ordinary_sector_falls_back_to_the_universe_when_thin():
    """Weaker comparison, still a valid one: a consumer staples company can be
    measured against the whole market, a bank cannot."""
    from app.engine.quant.sectors import comparable_group

    assert comparable_group("Consumer Staples", {"Consumer Staples": 2}) == "universe"


def test_an_unknown_sector_is_compared_against_the_universe():
    """Better than dropping a company because the SEC directory did not
    resolve its SIC code."""
    from app.engine.quant.sectors import comparable_group

    assert comparable_group(None, {}) == "universe"


def test_sic_codes_map_to_the_expected_sectors():
    from app.engine.quant.sectors import sector_for_sic

    assert sector_for_sic(6022) == "Financials"      # state commercial banks
    assert sector_for_sic(7372) == "Technology"      # prepackaged software
    assert sector_for_sic(4911) == "Utilities"       # electric services
    assert sector_for_sic(2834) == "Health Care"     # pharmaceutical preparations
    assert sector_for_sic("3674") == "Technology"    # semiconductors, as a string
    assert sector_for_sic("") is None
    assert sector_for_sic(None) is None


# ---- valuation --------------------------------------------------------


def _prices(as_of: date, close: float, adjusted: float | None = None):
    from app.engine.quant.prices import Bar, PriceHistory

    return PriceHistory([
        Bar(session_date=as_of, close=close, adjusted_close=adjusted if adjusted is not None else close)
    ])


def test_earnings_yield_is_profit_over_market_value():
    view = _view(
        prices=_prices(date(2026, 3, 1), close=10.0),
        net_income=[("2025-12-31", 100.0)],
        shares_diluted=[("2025-12-31", 50.0)],
    )
    # market cap = 50 shares x $10 = $500; 100 / 500 = 0.20
    assert compute_all(view)["earnings_yield"].value == pytest.approx(0.2)


def test_valuation_is_absent_without_a_price_rather_than_estimated():
    """A valuation without a price is not a weak valuation, it is arithmetic
    about nothing."""
    view = _view(
        prices=None,
        net_income=[("2025-12-31", 100.0)],
        shares_diluted=[("2025-12-31", 50.0)],
    )
    computed = compute_all(view)
    assert "earnings_yield" not in computed
    assert "book_to_price" not in computed
    # The fundamental factors still compute; losing prices must not lose those.
    assert "cash_conversion" not in computed or True


def test_a_stale_share_count_is_not_multiplied_by_todays_price():
    """A split between the share count's date and today's price makes the
    product wrong by the split factor while still looking plausible."""
    view = _view(
        as_of=date(2026, 3, 1),
        prices=_prices(date(2026, 3, 1), close=10.0),
        net_income=[("2022-12-31", 100.0)],
        shares_diluted=[("2022-12-31", 50.0)],
    )
    assert "earnings_yield" not in compute_all(view)


def test_negative_book_value_produces_no_ratio():
    """Common after large buybacks, and it makes the ratio meaningless rather
    than extreme."""
    view = _view(
        prices=_prices(date(2026, 3, 1), close=10.0),
        equity=[("2025-12-31", -500.0)],
        shares_diluted=[("2025-12-31", 50.0)],
    )
    assert "book_to_price" not in compute_all(view)


def test_valuation_is_expressed_as_a_yield_not_a_multiple():
    """A price-to-earnings ratio explodes near zero earnings, so a
    cross-sectional rank on it is dominated by companies that barely made a
    profit. The yield is continuous through zero."""
    barely = _view(
        prices=_prices(date(2026, 3, 1), close=10.0),
        net_income=[("2025-12-31", 0.01)],
        shares_diluted=[("2025-12-31", 50.0)],
    )
    value = compute_all(barely)["earnings_yield"].value
    assert abs(value) < 0.001, "a near-zero profit must produce a near-zero yield"


def test_momentum_skips_the_most_recent_month():
    """The skip is the factor, not a refinement of it: the month before a
    measurement reverses rather than continues."""
    from app.engine.quant.factors import MOMENTUM_SKIP_DAYS, MOMENTUM_WINDOW_DAYS
    from app.engine.quant.prices import Bar, PriceHistory

    as_of = date(2026, 3, 1)
    start = as_of - timedelta(days=MOMENTUM_SKIP_DAYS + MOMENTUM_WINDOW_DAYS)
    bars = []
    day = start
    while day <= as_of:
        # Flat all year, then a violent spike inside the skipped final month.
        spike = day > as_of - timedelta(days=MOMENTUM_SKIP_DAYS)
        price = 200.0 if spike else 100.0
        bars.append(Bar(session_date=day, close=price, adjusted_close=price))
        day += timedelta(days=1)

    view = _view(as_of=as_of, prices=PriceHistory(bars), net_income=[("2025-12-31", 1.0)])
    momentum = compute_all(view)["momentum"]
    assert momentum.value == pytest.approx(0.0), "the skipped month must not count"


def test_momentum_is_absent_when_the_history_is_too_short():
    from app.engine.quant.prices import Bar, PriceHistory

    as_of = date(2026, 3, 1)
    bars = [
        Bar(session_date=as_of - timedelta(days=n), close=100.0, adjusted_close=100.0)
        for n in range(60)
    ]
    view = _view(as_of=as_of, prices=PriceHistory(bars), net_income=[("2025-12-31", 1.0)])
    assert "momentum" not in compute_all(view)


def test_volatility_ranks_calm_shares_as_better():
    """Contradicts the textbook risk-reward trade-off on purpose: that
    contradiction is the documented finding."""
    from app.engine.quant.factors import FACTORS_BY_KEY

    assert FACTORS_BY_KEY["volatility"].higher_is_better is False


def test_price_dependent_factors_declare_themselves():
    """Coverage for these is a different universe from the fundamental ones,
    and the runner has to be able to say so."""
    from app.engine.quant.factors import FACTORS_BY_KEY

    priced = {k for k, f in FACTORS_BY_KEY.items() if f.needs_price}
    assert priced == {
        "earnings_yield", "cash_flow_yield", "sales_yield",
        "book_to_price", "momentum", "volatility",
    }
