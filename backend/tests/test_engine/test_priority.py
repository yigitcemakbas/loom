"""Ranking tests. The feed's whole value is that the top item is the one worth
reading first, so these assert relative order rather than exact scores."""

from datetime import datetime, timedelta, timezone

from app.engine.priority import score
from app.models.signal import SignalType

NOW = datetime(2026, 8, 24, tzinfo=timezone.utc)


def test_verifiable_signal_outranks_a_judgement_call():
    """A risk diff is checkable against the prior filing; sentiment is an
    opinion. At equal confidence the checkable one must rank higher."""
    risk = score(SignalType.QOQ_ANOMALY, 0.8, NOW, NOW)
    sentiment = score(SignalType.SENTIMENT_SHIFT, 0.8, NOW, NOW)
    assert risk > sentiment


def test_recent_beats_stale_at_equal_quality():
    fresh = score(SignalType.NEW_RISK_FACTOR, 0.8, NOW, NOW)
    old = score(SignalType.NEW_RISK_FACTOR, 0.8, NOW - timedelta(days=365), NOW)
    assert fresh > old
    assert old > 0  # decays, never disappears


def test_confidence_scales_the_score():
    assert score(SignalType.NEW_RISK_FACTOR, 0.9, NOW, NOW) > score(
        SignalType.NEW_RISK_FACTOR, 0.3, NOW, NOW
    )


def test_confidence_is_clamped():
    assert score(SignalType.NEW_RISK_FACTOR, 5.0, NOW, NOW) <= 1.0
    assert score(SignalType.NEW_RISK_FACTOR, -2.0, NOW, NOW) >= 0.0


def test_naive_timestamps_do_not_crash():
    """published_at can arrive without tzinfo; that must not raise."""
    assert score(SignalType.NOTABLE_QUOTE, 0.5, datetime(2026, 1, 1), NOW) > 0
