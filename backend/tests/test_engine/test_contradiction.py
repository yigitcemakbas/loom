"""Disagreement between Loom's own sources.

Two failure modes, opposite in shape. Firing on weak evidence produces a
contradiction on every company and teaches a reader to ignore the section.
Firing on two halves of the same document produces one that is not a
contradiction at all, because a 10-K is written to sound confident about risks
it is simultaneously disclosing.
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.engine.contradiction import (
    STRONG_PERCENTILE,
    STRONG_TONE,
    WEAK_PERCENTILE,
    find_contradictions,
)
from app.models.signal import Signal, SignalType

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


def _sig(signal_type, *, sentiment=None, direction=None, days_ago=1) -> Signal:
    return Signal(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        signal_type=signal_type,
        summary="A finding.",
        sentiment_score=sentiment,
        market_direction=direction,
        confidence=0.8,
        priority=1.0,
        occurred_at=NOW - timedelta(days=days_ago),
        signal_metadata={},
    )


def _keys(found) -> set[str]:
    return {c.key for c in found}


# ---- the disagreements worth surfacing --------------------------------


def test_confident_tone_over_poor_cash_quality_is_surfaced():
    signals = [_sig(SignalType.SENTIMENT_SHIFT, sentiment=0.6)]
    found = find_contradictions(signals, {"accruals": 0.02})

    assert "tone_vs_cash" in _keys(found)
    contradiction = next(c for c in found if c.key == "tone_vs_cash")
    # Both sides named, so a reader can check rather than take it on faith.
    assert contradiction.says_better and contradiction.says_worse
    assert contradiction.why_it_matters


def test_confident_tone_against_insider_selling_is_surfaced():
    signals = [
        _sig(SignalType.SENTIMENT_SHIFT, sentiment=0.5),
        _sig(SignalType.INSIDER_ACTIVITY, direction="negative"),
    ]
    assert "tone_vs_insiders" in _keys(find_contradictions(signals, {}))


def test_fast_growth_over_poor_cash_quality_is_surfaced():
    """Fast growth creates the room for aggressive revenue recognition, and
    this is what that combination looks like on the statements."""
    found = find_contradictions([], {"revenue_growth": 0.95, "accruals": 0.05})
    assert "growth_vs_quality" in _keys(found)


def test_high_returns_against_fast_expansion_is_surfaced():
    """Today's return is earned on yesterday's smaller asset base."""
    found = find_contradictions([], {"asset_growth": 0.04, "return_on_assets": 0.99})
    assert "expansion_vs_returns" in _keys(found)


def test_a_positive_reading_against_weak_reported_numbers_is_surfaced():
    found = find_contradictions([], {"composite": 0.1}, stance="positive")
    assert "reading_vs_numbers" in _keys(found)


def test_a_negative_reading_against_strong_numbers_is_surfaced_the_other_way():
    found = find_contradictions([], {"composite": 0.95}, stance="negative")
    contradiction = next(c for c in found if c.key == "reading_vs_numbers")
    assert "strong" in contradiction.headline.lower()


# ---- what must not fire -----------------------------------------------


def test_mild_tone_against_mild_weakness_is_not_a_contradiction():
    """A rule that fires on weak evidence fires constantly."""
    signals = [_sig(SignalType.SENTIMENT_SHIFT, sentiment=STRONG_TONE - 0.05)]
    assert find_contradictions(signals, {"accruals": WEAK_PERCENTILE + 0.05}) == []


def test_a_middling_factor_reading_is_not_one_side_of_anything():
    signals = [_sig(SignalType.SENTIMENT_SHIFT, sentiment=0.9)]
    assert find_contradictions(signals, {"accruals": 0.5}) == []


def test_a_missing_factor_is_silence_not_a_middling_reading():
    """Absent keys are absent. Defaulting them would suppress real
    contradictions and invent fake ones in equal measure."""
    signals = [_sig(SignalType.SENTIMENT_SHIFT, sentiment=0.9)]
    assert find_contradictions(signals, {}) == []


def test_tone_is_read_only_from_sentiment_findings():
    """Reading tone off the other finding types would double-count the same
    documents through a second channel and make every company look
    self-contradictory."""
    signals = [_sig(SignalType.NOTABLE_QUOTE, sentiment=0.9, direction="positive")]
    assert find_contradictions(signals, {"accruals": 0.01}) == []


def test_an_undirectional_stance_is_not_contradicted_by_anything():
    assert find_contradictions([], {"composite": 0.02}, stance="mixed") == []
    assert find_contradictions([], {"composite": 0.02}, stance="insufficient") == []


def test_one_expensive_measure_alone_does_not_contradict_momentum():
    """A single valuation reading is one measure disagreeing, not the numbers
    disagreeing."""
    found = find_contradictions([], {"momentum": 0.95, "earnings_yield": 0.05})
    assert "price_vs_value" not in _keys(found)


def test_momentum_against_several_expensive_measures_does():
    found = find_contradictions(
        [], {"momentum": STRONG_PERCENTILE, "earnings_yield": 0.05, "sales_yield": 0.03},
    )
    assert "price_vs_value" in _keys(found)


def test_a_contradiction_never_claims_a_direction():
    """It is a statement that the evidence is inconsistent, not a verdict.
    Assigning it a direction would invent the synthesis this avoids."""
    found = find_contradictions(
        [_sig(SignalType.SENTIMENT_SHIFT, sentiment=0.7)], {"accruals": 0.01},
    )
    for contradiction in found:
        assert not hasattr(contradiction, "direction")
        assert not hasattr(contradiction, "score")
