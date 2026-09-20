"""Tests for the measurement loop.

A harness that flatters the thing it measures is worse than no harness, so most
of these pin the guards rather than the arithmetic: benchmark subtraction,
entry strictly after the event, clustering, the naive baseline, and the
multiple-testing correction. Each of those, left out, makes a null result look
like a discovery.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.engine.evaluation import (
    MIN_SAMPLE_FOR_VERDICT,
    Outcome,
    cluster_by_event,
    forward_return,
    multiple_testing_threshold,
    spearman,
    summarise,
    survives_multiple_testing,
)

DAY = 86_400
T0 = datetime(2026, 6, 1, tzinfo=timezone.utc)


def _series(closes, start_ts=None):
    start = start_ts if start_ts is not None else T0.timestamp()
    return SimpleNamespace(
        points=[SimpleNamespace(t=start + i * DAY, c=c) for i, c in enumerate(closes)],
        currency="USD",
    )


def _outcome(direction, abnormal, ticker="AAA", day=0, strength=1.0, sid=None):
    return Outcome(
        subject_id=sid or f"{ticker}-{day}-{direction}",
        ticker=ticker,
        occurred_at=T0 + timedelta(days=day),
        predicted_direction=direction,
        strength=strength,
        abnormal_return_pct=abnormal,
        sessions=1,
    )


# ---- forward returns -------------------------------------------------------


def test_entry_is_strictly_after_the_event():
    """A judgement timestamped inside a session cannot be traded at that
    session's close. Measuring from it credits the engine with a move that had
    already happened, which is lookahead bias in its purest form."""
    series = _series([100.0, 110.0, 121.0])
    # Event lands exactly on the first session's timestamp.
    result = forward_return(series, T0, sessions=1)
    assert result is not None
    change, _entry, _exit = result
    # Entry is session 1 (110), exit session 2 (121): +10%, not +21%.
    assert change == pytest.approx(10.0)


def test_window_running_past_available_data_returns_nothing():
    """Silently shortening the window would mix horizons and report the
    mixture as if it were the requested one."""
    assert forward_return(_series([100.0, 101.0]), T0, sessions=5) is None


def test_missing_series_is_not_an_error():
    assert forward_return(None, T0, sessions=1) is None


# ---- clustering ------------------------------------------------------------


def test_findings_from_one_filing_collapse_to_one_observation():
    """Six findings from one document are one test, not six. Counting them
    separately inflates the sample and shrinks every confidence interval."""
    same_day = [_outcome("negative", -1.0, day=0, sid=f"s{i}") for i in range(6)]
    assert len(cluster_by_event(same_day)) == 1


def test_clustering_keeps_distinct_companies_and_days_apart():
    outcomes = [
        _outcome("negative", -1.0, ticker="AAA", day=0),
        _outcome("negative", -1.0, ticker="BBB", day=0),
        _outcome("negative", -1.0, ticker="AAA", day=1),
    ]
    assert len(cluster_by_event(outcomes)) == 3


def test_clustered_direction_is_a_weighted_vote():
    """One high-priority bullish finding should outvote two weak bearish ones,
    matching how the brief folds a stance."""
    clustered = cluster_by_event([
        _outcome("positive", -1.0, strength=0.9, sid="a"),
        _outcome("negative", -1.0, strength=0.1, sid="b"),
        _outcome("negative", -1.0, strength=0.1, sid="c"),
    ])
    assert clustered[0].predicted_direction == "positive"


def test_opposing_findings_of_equal_weight_cluster_to_neutral():
    clustered = cluster_by_event([
        _outcome("positive", 0.0, strength=0.5, sid="a"),
        _outcome("negative", 0.0, strength=0.5, sid="b"),
    ])
    assert clustered[0].predicted_direction == "neutral"


# ---- scoring ---------------------------------------------------------------


def test_correctness_is_judged_on_abnormal_return():
    assert _outcome("negative", -2.0).correct is True
    assert _outcome("negative", +2.0).correct is False
    assert _outcome("positive", +2.0).correct is True


def test_neutral_calls_are_not_scored_as_right_or_wrong():
    assert _outcome("neutral", -5.0).correct is None


def test_baseline_is_the_dominant_direction_not_fifty_percent():
    """In a falling period an always-bearish caller scores well above half. A
    hit rate reported without that comparison reads as skill when it is drift."""
    outcomes = [_outcome("negative", -1.0, day=i) for i in range(8)]
    outcomes += [_outcome("negative", +1.0, day=i + 100) for i in range(2)]
    report = summarise("t", 1, outcomes, skipped=0)
    assert report.baseline_hit_rate == pytest.approx(0.8)
    assert report.hit_rate == pytest.approx(0.8)
    assert report.edge_over_baseline == pytest.approx(0.0), "no edge over always-bearish"


def test_spread_is_bullish_minus_bearish():
    outcomes = [_outcome("positive", 2.0, day=1), _outcome("negative", -2.0, day=2)]
    assert summarise("t", 1, outcomes, skipped=0).spread == pytest.approx(4.0)


def test_small_sample_refuses_to_characterise_itself():
    outcomes = [_outcome("positive", 5.0, day=i) for i in range(4)]
    report = summarise("t", 1, outcomes, skipped=0)
    assert not report.has_enough_data
    assert "too few" in report.verdict()


def test_large_sample_with_no_signal_says_so():
    # Returns vary, but vary identically for both directions, so there is
    # dispersion to measure and no relationship to find.
    spread_of_returns = [-2.0, -1.0, 0.5, 1.0, 2.5]
    outcomes = []
    for i in range(MIN_SAMPLE_FOR_VERDICT + 10):
        outcomes.append(
            _outcome(
                "positive" if i % 2 else "negative",
                spread_of_returns[i % len(spread_of_returns)],
                day=i,
            )
        )
    report = summarise("t", 1, outcomes, skipped=0)
    assert report.has_enough_data
    assert "No evidence" in report.verdict() or "chance" in report.verdict()


# ---- statistics ------------------------------------------------------------


def test_spearman_is_rank_based_not_magnitude_based():
    assert spearman([1, 2, 3, 4], [10, 200, 3000, 40000]) == pytest.approx(1.0)


def test_spearman_handles_ties_without_arbitrary_ordering():
    assert spearman([1, 1, 1, 1], [4, 3, 2, 1]) is None


def test_spearman_needs_a_minimum_sample():
    assert spearman([1, 2], [2, 1]) is None


def test_multiple_testing_threshold_rises_with_the_number_of_tries():
    one = multiple_testing_threshold(1)
    six = multiple_testing_threshold(6)
    assert one == pytest.approx(1.96, abs=0.02)
    assert six > one
    assert six == pytest.approx(2.64, abs=0.05)


def test_a_promising_result_does_not_survive_six_tries():
    """The exact trap this guards: sweeping horizons and keeping the best."""
    reports = [
        summarise("s", h, [_outcome("positive", 1.0, day=h)], skipped=0)
        for h in range(6)
    ]
    reports[2].t_statistic = 2.1   # would pass a naive 2.0 bar
    assert survives_multiple_testing(reports) == []


def test_a_strong_result_does_survive():
    reports = [
        summarise("s", h, [_outcome("positive", 1.0, day=h)], skipped=0)
        for h in range(6)
    ]
    reports[2].t_statistic = 3.5
    assert survives_multiple_testing(reports) == ["s@2"]
