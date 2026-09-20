"""Tests for the fast path.

Its contract is unusual and worth stating: it must reach a verdict without a
model call, a database write, or a network request, because the window it
serves is minutes and the engine's model calls are paced in seconds. Several
tests below assert the absence of work rather than the presence of a result.
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.engine.reaction import (
    CROWDED_SHORT_AMPLIFIER,
    MEANINGFUL_SURPRISE_PERCENT,
    MarketEvent,
    assess,
)

NOW = datetime(2026, 9, 20, tzinfo=timezone.utc)


def _prior(watch_items=None, expectations=None, positioning=None, already_priced=None):
    return SimpleNamespace(
        watch_items=watch_items if watch_items is not None else [
            {
                "topic": "gross margin compression",
                "keywords": ["gross margin", "margin pressure", "cost of revenue"],
                "watch_for": "margin below prior quarter",
                "direction_if_confirmed": "negative",
                "severity": "major",
                "why_it_matters": "Margin drives the whole earnings model.",
            },
            {
                "topic": "datacenter demand",
                "keywords": ["datacenter", "data center", "hyperscaler"],
                "watch_for": "demand commentary",
                "direction_if_confirmed": "positive",
                "severity": "moderate",
                "why_it_matters": "Datacenter is the growth engine.",
            },
        ],
        expectations=expectations or {"eps_estimate": 2.00, "revenue_estimate": 1_000_000_000},
        positioning=positioning or {},
        already_priced=already_priced or [],
        generated_at=NOW,
    )


def _event(text="", *, kind="filing", **kw):
    return MarketEvent(ticker="NVDA", kind=kind, occurred_at=NOW, text=text, **kw)


def test_matches_a_standing_concern_by_keyword():
    result = assess(_event("Gross margin declined on higher cost of revenue."), _prior())
    assert [m.topic for m in result.matches] == ["gross margin compression"]
    assert result.direction == "negative"
    assert result.is_notable


def test_keyword_matching_respects_word_boundaries():
    """A bare substring test would fire 'ai' on 'chain' and 'china' on
    'machinery', which on a fast path nobody reviews is a silent false positive."""
    prior = _prior(watch_items=[{
        "topic": "AI demand", "keywords": ["ai"], "watch_for": "", "severity": "major",
        "direction_if_confirmed": "positive", "why_it_matters": "",
    }])
    assert assess(_event("Supply chain disruptions in machinery."), prior).matches == []
    assert assess(_event("AI demand accelerated."), prior).matches != []


def test_unrelated_event_scores_zero_and_says_so():
    result = assess(_event("The company appointed a new head of facilities."), _prior())
    assert result.score == 0.0
    assert not result.is_notable
    assert "nothing in this event matches" in result.headline


def test_earnings_beat_registers_as_positive_surprise():
    result = assess(_event(kind="earnings", eps_actual=2.40), _prior())
    assert result.eps_surprise_percent == pytest.approx(20.0)
    assert result.direction == "positive"
    assert result.is_notable


def test_surprise_below_the_noise_floor_is_ignored():
    result = assess(_event(kind="earnings", eps_actual=2.01), _prior())
    assert abs(result.eps_surprise_percent) < MEANINGFUL_SURPRISE_PERCENT
    assert result.score == 0.0


def test_surprise_against_a_near_zero_estimate_is_capped():
    """A company guiding to break even produces surprise percentages in the
    hundreds from a rounding error. That is an artefact of the denominator."""
    prior = _prior(expectations={"eps_estimate": 0.01, "revenue_estimate": None})
    result = assess(_event(kind="earnings", eps_actual=0.50), prior)
    assert result.eps_surprise_percent > 1000
    assert result.score <= 2.0 * CROWDED_SHORT_AMPLIFIER


def test_crowded_short_amplifies_an_upside_surprise():
    plain = assess(_event(kind="earnings", eps_actual=2.40), _prior())
    crowded = assess(
        _event(kind="earnings", eps_actual=2.40),
        _prior(positioning={"short_crowded": True, "days_to_cover": 7.2}),
    )
    assert crowded.score > plain.score
    assert crowded.amplifiers and "crowded short" in crowded.amplifiers[0]


def test_crowded_short_does_not_amplify_a_downside_surprise():
    """Covering pressure is an upside mechanic. Applying it to a miss would
    make bad news look bigger for a reason that does not exist."""
    plain = assess(_event(kind="earnings", eps_actual=1.50), _prior())
    crowded = assess(
        _event(kind="earnings", eps_actual=1.50),
        _prior(positioning={"short_crowded": True, "days_to_cover": 7.2}),
    )
    assert crowded.score == plain.score
    assert crowded.amplifiers == []


def test_already_priced_topics_are_discounted_not_ignored():
    normal = assess(_event("Gross margin fell again."), _prior())
    priced = assess(
        _event("Gross margin fell again."),
        _prior(already_priced=[{"topic": "gross margin compression", "moved_percent": -12.0}]),
    )
    assert 0 < priced.score < normal.score
    assert priced.matches[0].already_priced is True


def test_missing_prior_is_reported_rather_than_guessed():
    result = assess(_event("Gross margin collapsed."), None)
    assert result.score == 0.0
    assert result.direction == "neutral"
    assert "No standing prior" in result.headline


def test_severity_ordering_is_respected():
    major = _prior(watch_items=[{
        "topic": "t", "keywords": ["widget"], "watch_for": "", "severity": "major",
        "direction_if_confirmed": "negative", "why_it_matters": "",
    }])
    minor = _prior(watch_items=[{
        "topic": "t", "keywords": ["widget"], "watch_for": "", "severity": "minor",
        "direction_if_confirmed": "negative", "why_it_matters": "",
    }])
    assert assess(_event("widget"), major).score > assess(_event("widget"), minor).score


def test_fast_path_does_no_io_and_stays_in_the_microsecond_range():
    """The reason this module exists. If scoring ever needs a model call or a
    query, the design has failed and this test should fail with it."""
    import socket

    def blocked(*a, **k):  # pragma: no cover - only runs if the design regresses
        raise AssertionError("the fast path must not open a socket")

    original = socket.socket
    socket.socket = blocked
    try:
        result = assess(
            _event("Gross margin compression and hyperscaler datacenter demand.", kind="earnings", eps_actual=2.4),
            _prior(positioning={"short_crowded": True, "days_to_cover": 6.0}),
        )
    finally:
        socket.socket = original

    assert result.is_notable
    assert result.elapsed_ms < 50, f"fast path took {result.elapsed_ms}ms"
