"""The factor arithmetic, and the cases where it must decline to answer.

Most of these pin a refusal rather than a value. A factor that returns a number
from bad inputs is worse than one that returns nothing, because the number
enters a composite, gets ranked against a hundred real companies, and arrives
in front of a reader with no mark on it saying where it came from.
"""

from datetime import date

import pytest

from app.engine.quant.crosssection import MIN_UNIVERSE, rank_universe
from app.engine.quant.composite import (
    MIN_FACTORS_FOR_COMPOSITE,
    build_composite,
    build_f_score,
)
from app.engine.quant.crosssection import Ranked
from app.engine.quant.factors import FACTORS, FACTORS_BY_KEY, compute_all
from app.engine.quant.series import INSTANT, YEAR, FactSeries, Observation


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
    assert compute_all(series)["accruals"].value == pytest.approx(0.04)


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
    assert compute_all(grew)["accruals"].value == pytest.approx(0.04)


def test_dilution_is_measured_against_the_prior_share_count():
    series = _series(shares_diluted=[("2024-12-31", 1000.0), ("2025-12-31", 1100.0)])
    assert compute_all(series)["net_share_issuance"].value == pytest.approx(0.1)


def test_a_buyback_reads_as_negative_issuance():
    series = _series(shares_diluted=[("2024-12-31", 1000.0), ("2025-12-31", 900.0)])
    assert compute_all(series)["net_share_issuance"].value == pytest.approx(-0.1)


# ---- the refusals -----------------------------------------------------


def test_cash_conversion_is_undefined_against_a_loss():
    """A negative denominator flips the sign, which would rank a loss-making
    company as having excellent cash conversion."""
    series = _series(
        net_income=[("2025-12-31", -50.0)],
        operating_cash_flow=[("2025-12-31", -20.0)],
    )
    assert "cash_conversion" not in compute_all(series)


def test_a_near_zero_denominator_produces_no_factor_rather_than_a_huge_one():
    """One company with near-zero assets would otherwise dominate every
    cross-sectional rank it appears in."""
    series = _series(
        net_income=[("2025-12-31", 100.0)],
        operating_cash_flow=[("2025-12-31", 10.0)],
        assets=[("2024-12-31", 0.2), ("2025-12-31", 0.1)],
    )
    assert "accruals" not in compute_all(series)


def test_a_missing_input_yields_no_factor_rather_than_zero():
    """Zero is a reading. Missing is the absence of one, and a composite that
    cannot tell them apart scores a company on data it never had."""
    series = _series(net_income=[("2025-12-31", 100.0)])
    computed = compute_all(series)
    assert "accruals" not in computed
    assert "cash_conversion" not in computed


def test_margin_change_refuses_mismatched_periods():
    """Both margins must come from the same pair of periods, or the change is
    measuring the calendar rather than the business."""
    series = _series(
        revenue=[("2024-12-31", 100.0), ("2025-12-31", 120.0)],
        operating_income=[("2023-12-31", 10.0), ("2024-06-30", 15.0)],
    )
    assert "operating_margin_change" not in compute_all(series)


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
