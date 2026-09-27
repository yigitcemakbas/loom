"""Scoring Loom's own verdicts.

The methodology matters more than the arithmetic here, because every mistake
available inflates the result. Counting nine regenerated briefs as nine calls
multiplies the sample. Entering at the close of the session a verdict was
issued in credits the engine with a move that had already happened. Scoring a
refusal as a wrong answer punishes the engine for its honesty.
"""

from datetime import datetime, timedelta, timezone

from app.engine.verdicts import (
    HORIZONS,
    NUMBERS_NEGATIVE,
    NUMBERS_POSITIVE,
    Call,
    ComponentResult,
    VerdictReport,
    _DIRECTION,
    _STRENGTH,
)
from app.models.brief import Stance

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


# ---- which verdicts count ---------------------------------------------


def test_declining_to_answer_is_not_scored_as_a_wrong_answer():
    """Mixed, quiet and insufficient are Loom refusing to call it. Scoring
    them would punish the engine for the honesty that makes it trustworthy."""
    for stance in (Stance.MIXED, Stance.QUIET, Stance.INSUFFICIENT):
        assert stance not in _DIRECTION


def test_every_directional_stance_is_scored():
    for stance in (Stance.POSITIVE, Stance.STRONG_POSITIVE,
                   Stance.NEGATIVE, Stance.STRONG_NEGATIVE):
        assert stance in _DIRECTION


def test_a_confident_wrong_call_costs_more_than_a_tentative_one():
    """Otherwise "clearly negative" and "leaning negative" are the same claim,
    and the confidence Loom expresses means nothing."""
    assert abs(_STRENGTH[Stance.STRONG_NEGATIVE]) > abs(_STRENGTH[Stance.NEGATIVE])
    assert _STRENGTH[Stance.STRONG_POSITIVE] > _STRENGTH[Stance.POSITIVE]


def test_direction_and_strength_agree_on_sign():
    for stance, direction in _DIRECTION.items():
        assert (_STRENGTH[stance] > 0) == (direction > 0)


# ---- the component split ----------------------------------------------


def test_the_two_halves_are_scored_separately():
    """A blended number is the least useful shape the answer could take: a null
    says nothing about what to fix, a positive nothing about what to keep."""
    report = VerdictReport(results=[
        ComponentResult(component="read", horizon=1, calls=50, t_statistic=0.1),
        ComponentResult(component="numbers", horizon=1, calls=50, t_statistic=3.5),
    ])
    components = {r.component for r in report.results}
    assert components == {"read", "numbers"}


def test_the_numbers_middle_is_a_refusal_not_a_call():
    """Mirrors how the stance treats its own middle. A company ranked in the
    middle of its peers is the accounts declining to answer."""
    assert NUMBERS_NEGATIVE < 0.5 < NUMBERS_POSITIVE


# ---- what counts as a result ------------------------------------------


def test_a_thin_sample_reports_itself_as_thin():
    result = ComponentResult(component="read", horizon=1, calls=12, t_statistic=4.0)
    assert "too few" in result.verdict()


def test_a_weak_t_is_reported_as_chance_however_large_the_spread():
    result = ComponentResult(
        component="read", horizon=5, calls=200, spread=3.5, t_statistic=1.4,
    )
    assert "chance" in result.verdict()


def test_a_strong_result_is_still_not_called_tradeable():
    """There are no transaction costs, borrow costs or slippage anywhere in
    this measurement."""
    result = ComponentResult(
        component="numbers", horizon=21, calls=200, spread=2.0, t_statistic=3.4,
    )
    assert "not yet worth trading" in result.verdict()


def test_every_component_and_horizon_is_a_separate_chance_to_be_lucky():
    report = VerdictReport(results=[
        ComponentResult(component="read", horizon=h, calls=100, t_statistic=2.5)
        for h in HORIZONS
    ], threshold=2.6)
    assert report.survivors() == []


def test_a_result_below_the_sample_floor_never_survives():
    """However large its t-statistic."""
    report = VerdictReport(results=[
        ComponentResult(component="read", horizon=1, calls=9, t_statistic=9.0),
    ], threshold=2.0)
    assert report.survivors() == []


# ---- horizons ----------------------------------------------------------


def test_a_month_is_measured_as_well_as_a_day():
    """The verdict is built from quarterly disclosures. A one-day horizon asks
    it to do something it was never designed for."""
    assert max(HORIZONS) >= 21


def test_a_call_carries_when_it_was_made():
    """Entry is strictly after that moment; a verdict issued during a session
    cannot be traded at that session's close."""
    call = Call(ticker="AAA", made_on=NOW, direction=1.0, strength=0.5, component="read")
    assert call.made_on == NOW
    assert call.abnormal_return is None
