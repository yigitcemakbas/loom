"""Guidance changes, and the direction the document stated.

Guidance was the one finding type whose direction the document says outright and
which Loom scored as unassessed anyway. The reason was real rather than an
oversight: nothing recorded *which* figure moved, and "capital investment
increased" and "operating margin increased" are the same sentence shape and
opposite news. Extraction now records the metric and the movement separately, so
the direction is derived from a stated fact instead of guessed from wording.

The abstentions below matter as much as the signs. A rule that resolved every
guidance change would be guessing on the ones that do not have an answer.
"""

from app.engine.direction import (
    _guidance_sign,
    _metric_polarity,
    can_be_directional,
    documentary_sign,
)
from app.models.signal import Signal, SignalType


def _guidance(**meta) -> Signal:
    return Signal(signal_type=SignalType.GUIDANCE_CHANGE, signal_metadata=meta)


# ---- the direction a raised or lowered figure states -----------------------


def test_raising_revenue_guidance_is_positive_and_lowering_it_is_negative():
    assert documentary_sign(_guidance(metric="revenue", movement="raised")) == 1.0
    assert documentary_sign(_guidance(metric="revenue", movement="lowered")) == -1.0


def test_the_same_movement_on_a_cost_figure_points_the_other_way():
    """The reason the metric has to be stored. Both sentences read "guidance
    raised"; one is good news and one is not."""
    assert documentary_sign(_guidance(metric="operating expenses", movement="raised")) == -1.0
    assert documentary_sign(_guidance(metric="operating expenses", movement="lowered")) == 1.0


# ---- where it abstains, and why --------------------------------------------


def test_a_metric_with_no_settled_polarity_is_left_unassessed():
    """Capital expenditure rising may be investment into demand or spending to
    stand still, and the guidance sentence does not say which. None is the
    honest answer, and the whole reason this rule did not exist before."""
    assert _guidance_sign({"metric": "capital expenditure", "movement": "raised"}) is None
    assert _guidance_sign({"metric": "headcount", "movement": "lowered"}) is None
    assert _metric_polarity("capital investment") is None


def test_an_unrecognised_metric_abstains_rather_than_defaulting():
    """An unknown figure has no polarity, and treating absence from a list as
    "higher is better" would invent a direction for anything unusual."""
    assert _metric_polarity("widget throughput") is None
    assert _guidance_sign({"metric": "widget throughput", "movement": "raised"}) is None


def test_reaffirming_or_initiating_guidance_states_no_direction():
    """Restating a number unchanged is not a move, and a first-ever forecast has
    nothing to be compared against."""
    assert _guidance_sign({"metric": "revenue", "movement": "reaffirmed"}) is None
    assert _guidance_sign({"metric": "revenue", "movement": "initiated"}) is None


def test_findings_written_before_the_metric_was_recorded_stay_unassessed():
    """No retrospective guessing. A finding with no metadata is a gap, and
    inferring its direction from the description would be the thing this change
    exists to avoid."""
    assert documentary_sign(_guidance()) is None
    assert documentary_sign(_guidance(movement="raised")) is None


# ---- withdrawal ------------------------------------------------------------


def test_withdrawing_guidance_is_negative_whatever_the_figure():
    """Signed without consulting the metric. Removing a forecast the company
    previously committed to narrows what a reader knows, and guidance is not
    generally suspended on good news."""
    assert _guidance_sign({"metric": "revenue", "movement": "withdrawn"}) == -1.0
    assert _guidance_sign({"metric": "capital expenditure", "movement": "withdrawn"}) == -1.0
    assert _guidance_sign({"movement": "withdrawn"}) == -1.0


# ---- sufficiency accounting ------------------------------------------------


def test_guidance_counts_as_a_finding_a_document_could_have_directed():
    """Listed on what the document could state, not on what Loom read. An
    unassessed guidance change should count against sufficiency rather than
    excuse itself, which is what distinguishes a gap from a quote."""
    assert can_be_directional(_guidance(metric="revenue", movement="raised"))
    assert can_be_directional(_guidance())
    assert not can_be_directional(Signal(signal_type=SignalType.NOTABLE_QUOTE))


def test_an_ambiguous_metric_is_checked_before_the_polarity_lists():
    """`capital investment` must not pick up a polarity from a later addition to
    the other lists; ambiguity wins by being tested first."""
    assert _metric_polarity("capital expenditure") is None
    assert _metric_polarity("investment") is None
