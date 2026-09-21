"""Tests for earnings figure extraction.

The hazard this guards is specific: a wrong number here does not raise, it
feeds a surprise percentage that reads as authoritative and that someone may
act on. So the tests care much more about refusing bad input than about
parsing good input.
"""

from types import SimpleNamespace

import pytest

from app.engine.earnings_extract import (
    MAX_CREDIBLE_SURPRISE_PERCENT,
    RESULTS_ITEM,
    comparable_eps,
    extract_figures,
    is_results_filing,
    looks_like_earnings,
    surprise_is_credible,
)
from app.engine.prompts.earnings_release import EarningsFigures


def _client(result):
    return SimpleNamespace(parse=lambda **k: result)


def _figures(**kw):
    base = dict(eps_gaap=None, eps_adjusted=None, revenue=None, confident=True)
    base.update(kw)
    return EarningsFigures(**base)


# ---- gating ----------------------------------------------------------------


def test_only_results_filings_trigger_extraction():
    assert is_results_filing(["2.02"])
    assert is_results_filing(["5.02", "2.02"])
    assert not is_results_filing(["8.01"])
    assert not is_results_filing([])


def test_results_item_is_the_sec_code_for_results():
    assert RESULTS_ITEM == "2.02"


def test_cover_sheet_without_figures_costs_no_model_call():
    """Some item 2.02 filings attach only an exhibit reference. Calling a model
    to read a cover sheet spends quota for a guaranteed null."""
    def fail(**k):  # pragma: no cover - only on regression
        raise AssertionError("must not call a model for a filing with no figures")

    assert extract_figures("Exhibit 99.1 is furnished herewith.", client=SimpleNamespace(parse=fail)) is None


def test_recognises_the_wording_real_releases_use():
    for text in (
        "GAAP and non-GAAP earnings per diluted share were $2.40",
        "Diluted EPS $1.38",
        "Total revenue of $57.0 billion",
        "Record non-GAAP EPS of $1.66",
    ):
        assert looks_like_earnings(text), text


# ---- which number to compare -----------------------------------------------


def test_adjusted_eps_is_preferred_over_gaap():
    """Consensus is quoted on an adjusted basis, so comparing a GAAP actual
    against it would manufacture a surprise out of an accounting difference."""
    value, basis = comparable_eps(_figures(eps_gaap=1.10, eps_adjusted=2.40))
    assert (value, basis) == (2.40, "adjusted")


def test_gaap_only_is_not_treated_as_comparable_to_consensus():
    """Checked against four real releases: where a non-GAAP figure existed the
    match was exact, and where only GAAP did it was wrong every time. Apple
    reported $2.02 GAAP against a $1.93 consensus while the comparable actual
    was $1.91, so comparing them turns a small miss into a beat and inverts the
    direction of the trade."""
    value, basis = comparable_eps(_figures(eps_gaap=2.02))
    assert value is None
    assert basis == "gaap_only_not_comparable"


def test_basis_is_reported_when_there_is_no_figure():
    assert comparable_eps(_figures()) == (None, "none")


# ---- refusing bad numbers --------------------------------------------------


def test_implausible_eps_is_discarded():
    """A quarterly diluted EPS of 4,200 is a parsing artefact or a figure in
    cents, not a company having an extraordinary quarter."""
    result = extract_figures("diluted EPS", client=_client(_figures(eps_gaap=4200.0)))
    assert result is None


def test_revenue_reported_in_millions_is_discarded_not_believed():
    """A release quoting '57,000' meaning millions would otherwise be read as
    fifty-seven thousand dollars of revenue and produce a catastrophic miss."""
    result = extract_figures("total revenue", client=_client(_figures(revenue=57_000.0)))
    assert result is None


def test_a_partial_reading_is_kept():
    """Revenue without EPS is still worth having; the surprise calculation uses
    whichever figures it has."""
    result = extract_figures(
        "total revenue", client=_client(_figures(revenue=57_000_000_000.0))
    )
    assert result is not None and result.revenue == pytest.approx(57e9)
    assert result.eps_gaap is None


def test_plausible_figures_survive_intact():
    result = extract_figures(
        "diluted EPS", client=_client(_figures(eps_adjusted=2.40, revenue=57_000_000_000.0))
    )
    assert result.eps_adjusted == pytest.approx(2.40)
    assert comparable_eps(result) == (2.40, "adjusted")


# ---- failure isolation -----------------------------------------------------


def test_model_failure_costs_the_figures_not_the_event():
    """The watcher still has keywords, positioning and the prior to work with.
    Losing an assessment because one extraction failed would be a bad trade."""
    def boom(**k):
        raise RuntimeError("provider down")

    assert extract_figures("diluted EPS", client=SimpleNamespace(parse=boom)) is None


def test_empty_model_response_is_not_an_error():
    assert extract_figures("diluted EPS", client=_client(None)) is None


# ---- the credibility net ---------------------------------------------------


def test_a_cumulative_period_figure_is_rejected_by_the_comparison():
    """The Amazon case. Its release carried a "Twelve Months Ended" column and
    the model took $5.75 from it against a $1.86 estimate. Every bound on the
    raw number passes, because $5.75 is an ordinary EPS. Only comparing it to
    consensus reveals a 209% beat that cannot be real."""
    assert not surprise_is_credible(5.75, 1.86)


def test_an_ordinary_beat_is_accepted():
    assert surprise_is_credible(2.22, 2.1384)


def test_a_large_but_believable_surprise_sits_inside_the_bound():
    assert surprise_is_credible(1.20, 1.00)
    assert MAX_CREDIBLE_SURPRISE_PERCENT == 25.0


def test_no_estimate_means_no_credible_surprise():
    """Without consensus there is nothing to be surprised against, and a bare
    actual must not be presented as a beat or a miss."""
    assert not surprise_is_credible(2.22, None)
    assert not surprise_is_credible(2.22, 0)
