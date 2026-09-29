"""The feedback loop, and the two guards that make connecting it safe.

`engine/evaluation.py` could measure whether findings predicted anything for most
of this project's life and gated nothing. `engine/priority.py` ranks findings by a
hand-written table of type weights that no outcome had ever checked. This module
is the wire between them, and most of what is tested here is its refusal to act.
"""

from app.engine.priority import TYPE_WEIGHTS, score
from app.engine.reliability import (
    MIN_OBSERVATIONS,
    MIN_T_STATISTIC,
    MULTIPLIER_CEILING,
    MULTIPLIER_FLOOR,
    Reliability,
    multiplier_for,
)
from app.models.signal import SignalType

from datetime import datetime, timezone

NOW = datetime(2026, 9, 29, tzinfo=timezone.utc)


def _record(**kw) -> Reliability:
    base = dict(
        signal_type="new_risk_factor", observations=MIN_OBSERVATIONS + 10,
        hit_rate=0.60, baseline_hit_rate=0.50, spread=1.2, t_statistic=3.0,
    )
    base.update(kw)
    return Reliability(**base)


# ---- the refusals ----------------------------------------------------------


def test_a_thin_record_changes_nothing():
    """The guard that matters today. Eight signal types against 695 findings puts
    every one below the floor, so switching this on moves nothing — which is the
    point, not a limitation. Fitting eight weights to a few dozen noisy
    observations each is the mistake quant/relevance.py refuses by name."""
    thin = _record(observations=MIN_OBSERVATIONS - 1)

    assert not thin.trusted
    assert thin.multiplier == 1.0


def test_a_spread_inside_the_range_chance_produces_changes_nothing():
    assert not _record(t_statistic=MIN_T_STATISTIC - 0.1).trusted
    assert _record(t_statistic=None).multiplier == 1.0


def test_an_unmeasured_type_keeps_the_weight_a_human_wrote():
    """An absent record is not a bad one. The hand-written weight encodes a stated
    reason about evidence quality, and silence must not override it."""
    assert multiplier_for(SignalType.NOTABLE_QUOTE, {}) == 1.0
    assert multiplier_for(SignalType.GUIDANCE_CHANGE, {"new_risk_factor": 1.3}) == 1.0


# ---- the bound -------------------------------------------------------------


def test_even_a_trusted_record_is_bounded():
    """This is a feedback loop whose measurement shares a corpus with the thing it
    measures. Unbounded gain would find any bias in that measurement and amplify
    it, so a record can only move a weight inside a band."""
    enormous = _record(hit_rate=0.99, baseline_hit_rate=0.40, t_statistic=9.0)
    dreadful = _record(hit_rate=0.10, baseline_hit_rate=0.60, t_statistic=-9.0)

    assert enormous.multiplier == MULTIPLIER_CEILING
    assert dreadful.multiplier == MULTIPLIER_FLOOR
    assert MULTIPLIER_FLOOR < 1.0 < MULTIPLIER_CEILING


def test_the_edge_is_measured_against_a_no_skill_baseline_not_against_zero():
    """A type can clear a t-test on a spread that is still worse than always
    guessing whichever direction dominated the period."""
    beats_chance_but_not_baseline = _record(hit_rate=0.55, baseline_hit_rate=0.70)

    assert beats_chance_but_not_baseline.edge < 0
    assert beats_chance_but_not_baseline.multiplier < 1.0


# ---- the wire ---------------------------------------------------------------


def test_priority_defaults_to_no_adjustment():
    """Every existing caller must score exactly as it did before the loop existed."""
    plain = score(SignalType.NEW_RISK_FACTOR, 0.9, NOW, now=NOW)
    explicit = score(SignalType.NEW_RISK_FACTOR, 0.9, NOW, now=NOW, reliability=1.0)

    assert plain == explicit


def test_a_trusted_record_moves_the_ranking():
    up = score(SignalType.NEW_RISK_FACTOR, 0.9, NOW, now=NOW, reliability=1.3)
    down = score(SignalType.NEW_RISK_FACTOR, 0.9, NOW, now=NOW, reliability=0.7)

    assert up > score(SignalType.NEW_RISK_FACTOR, 0.9, NOW, now=NOW) > down


def test_reliability_cannot_invert_the_evidence_quality_ordering():
    """The type table encodes a stated epistemology — a deterministic two-filing
    comparison outranks an unverifiable language judgement — and a measured edge on
    a few dozen observations should refine it, not overturn it.

    This test failed on the first attempt at [0.7, 1.3], where a best-case quote
    scored 0.78 against a worst-case risk diff at 0.70. The band was narrowed until
    the extremes could not cross. Adjacent types can still swap, which is intended:
    the table's fine gradations are judgement, its top-to-bottom ordering is the
    claim.
    """
    best_quote = TYPE_WEIGHTS[SignalType.NOTABLE_QUOTE] * MULTIPLIER_CEILING
    worst_risk_diff = TYPE_WEIGHTS[SignalType.NEW_RISK_FACTOR] * MULTIPLIER_FLOOR

    assert best_quote < worst_risk_diff
