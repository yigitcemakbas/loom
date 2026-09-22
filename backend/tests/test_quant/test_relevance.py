"""Conditional weighting: what a factor is worth for THIS company.

The rule this file mostly defends is that the weights are not fitted. Every
downweight states a reason that was true before any backtest ran. The
temptation after a bad result is to switch off whatever underperformed, which
would be fitting eighteen parameters to fifty-five observations and would
produce a beautiful in-sample score meaning nothing.
"""

import pytest

from app.engine.quant.relevance import (
    FULL,
    OFF,
    REDUCED,
    RND_INTENSITY_THRESHOLD,
    THEME_OF,
    THEMES,
    relevance_for,
    weighted_composite,
)
from app.engine.quant.factors import FACTORS_BY_KEY


def test_leverage_is_not_counted_against_a_bank():
    """Borrowed money is a bank's raw material. Rising leverage is expansion,
    not risk, and scoring it as risk is what put eight banks at the bottom of
    the first universe-wide run."""
    relevance = relevance_for("leverage_change", sector="Financials")
    assert relevance.weight == OFF
    assert relevance.reason, "a switched-off factor must say why"


def test_the_same_factor_counts_fully_for_an_operating_company():
    assert relevance_for("leverage_change", sector="Technology").weight == FULL


def test_cash_based_measures_are_off_for_financials():
    """Operating cash flow is routinely negative at a bank for reasons that say
    nothing about its health, which makes every ratio built on it noise."""
    for key in ("cash_conversion", "accruals", "cash_flow_yield"):
        assert relevance_for(key, sector="Financials").weight == OFF


def test_book_value_is_discounted_for_a_research_heavy_company():
    """Book value counts factories and not research, so an asset-light company
    looks expensive on it by construction."""
    heavy = relevance_for("book_to_price", sector="Technology", rnd_intensity=0.25)
    light = relevance_for("book_to_price", sector="Technology", rnd_intensity=0.01)

    assert heavy.weight == REDUCED
    assert light.weight == FULL


def test_a_discounted_factor_is_pulled_toward_the_middle_not_dropped():
    """It still says something; it is known to be distorted. Dropping it would
    lose the reading, and counting it fully would overstate it."""
    percentiles = {k: 0.0 for k in FACTORS_BY_KEY}
    percentiles["book_to_price"] = 0.0

    distorted = weighted_composite(
        percentiles, sector="Technology", rnd_intensity=0.30,
    )
    undistorted = weighted_composite(
        percentiles, sector="Technology", rnd_intensity=0.0,
    )
    assert distorted is not None and undistorted is not None
    # The discounted company is scored less harshly on a measure known to be
    # unfair to it.
    assert distorted.themes["valuation"] > undistorted.themes["valuation"]


def test_a_theme_is_not_weighted_by_how_many_factors_it_happens_to_contain():
    """There are four valuation measures and two price-behaviour measures, so
    an equal-weighted mean gave valuation twice the vote for no reason other
    than authorship."""
    good_value_only = {k: 0.5 for k in FACTORS_BY_KEY}
    for key in THEMES["valuation"]:
        good_value_only[key] = 1.0

    result = weighted_composite(good_value_only, sector="Technology")
    assert result is not None
    # Four perfect valuation readings lift one theme of seven, not four
    # eighteenths of the whole.
    assert result.themes["valuation"] == pytest.approx(1.0)
    assert result.score < 0.65, "one theme must not dominate the score"


def test_a_switched_off_factor_is_reported_rather_than_silently_missing():
    percentiles = {k: 0.5 for k in FACTORS_BY_KEY}
    result = weighted_composite(percentiles, sector="Financials")

    assert result is not None
    assert "leverage_change" in result.excluded
    assert result.excluded["leverage_change"]


def test_no_composite_from_too_few_themes():
    """A score folded from one theme is a measurement of one aspect wearing the
    name of a summary."""
    thin = {"accruals": 0.9, "cash_conversion": 0.9}
    assert weighted_composite(thin, sector="Technology") is None


def test_every_factor_belongs_to_exactly_one_theme():
    """A factor in no theme would be silently dropped from every score; a
    factor in two would be counted twice."""
    assigned = [key for keys in THEMES.values() for key in keys]
    assert len(assigned) == len(set(assigned)), "no factor may appear in two themes"
    assert set(assigned) == set(FACTORS_BY_KEY), "every factor must have a theme"
    assert all(THEME_OF[key] for key in FACTORS_BY_KEY)


def test_the_weights_were_not_fitted_to_the_backtest():
    """Volatility and gross profitability both ranked returns backwards over
    2021-2026. Neither is downweighted, because a five-year regime is not
    evidence about a measure."""
    for key in ("volatility", "gross_profitability", "momentum"):
        assert relevance_for(key, sector="Technology").weight == FULL
        assert relevance_for(key, sector="Financials").weight == FULL
