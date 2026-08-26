"""Summary aggregation tests. No database, pure function over Signal objects."""

import uuid
from datetime import datetime, timedelta, timezone

from app.engine.summary import WINDOW_DAYS, summarize
from app.models.signal import Signal, SignalType

COMPANY = uuid.uuid4()
NOW = datetime.now(timezone.utc)


def _signal(signal_type, priority, occurred_at, sentiment=None):
    return Signal(
        id=uuid.uuid4(), company_id=COMPANY, signal_type=signal_type,
        summary="x", confidence=0.8, priority=priority,
        sentiment_score=sentiment, occurred_at=occurred_at, signal_metadata={},
    )


def test_top_signal_is_highest_priority_regardless_of_age():
    old_but_important = _signal(SignalType.NEW_RISK_FACTOR, 0.9, NOW - timedelta(days=400))
    recent_but_minor = _signal(SignalType.NOTABLE_QUOTE, 0.2, NOW)
    result = summarize([old_but_important, recent_but_minor], [])
    assert result.top_signal is old_but_important


def test_risk_count_excludes_non_risk_types():
    signals = [
        _signal(SignalType.NEW_RISK_FACTOR, 0.5, NOW),
        _signal(SignalType.QOQ_ANOMALY, 0.5, NOW),
        _signal(SignalType.NOTABLE_QUOTE, 0.5, NOW),
        _signal(SignalType.SENTIMENT_SHIFT, 0.5, NOW),
    ]
    result = summarize(signals, [])
    assert result.risk_count == 2
    assert result.signal_count == 4


def test_signals_outside_window_are_not_counted_as_recent():
    stale = _signal(SignalType.NEW_RISK_FACTOR, 0.5, NOW - timedelta(days=WINDOW_DAYS + 30))
    result = summarize([stale], [])
    assert result.signal_count == 0
    assert result.risk_count == 0
    # But it can still be the top signal, recency and importance are separate questions.
    assert result.top_signal is stale


def test_trend_is_delta_between_last_two_sentiment_readings():
    series = [
        _signal(SignalType.SENTIMENT_SHIFT, 0.5, NOW - timedelta(days=60), sentiment=-0.3),
        _signal(SignalType.SENTIMENT_SHIFT, 0.5, NOW, sentiment=0.2),
    ]
    result = summarize(series, series)
    assert result.sentiment_score == 0.2
    assert round(result.sentiment_trend, 2) == 0.5


def test_trend_is_none_with_fewer_than_two_points():
    one = [_signal(SignalType.SENTIMENT_SHIFT, 0.5, NOW, sentiment=0.1)]
    result = summarize(one, one)
    assert result.sentiment_trend is None
    assert result.sentiment_score == 0.1


def test_empty_input_is_handled_cleanly():
    result = summarize([], [])
    assert result.top_signal is None
    assert result.sentiment_score is None
    assert result.sentiment_trend is None
    assert result.signal_count == 0
    assert result.last_signal_at is None


def test_sentiment_history_is_oldest_first_and_capped():
    # d descends 20->1 so occurred_at ascends oldest-to-newest; sentiment
    # is built to ascend in step with it, so "oldest-first" and "ascending
    # values" coincide here purely as a test-fixture convenience.
    series = [_signal(SignalType.SENTIMENT_SHIFT, 0.5, NOW - timedelta(days=d), sentiment=(20 - d) / 100) for d in range(20, 0, -1)]
    result = summarize(series, series)
    assert len(result.sentiment_history) <= 8
    assert result.sentiment_history == sorted(result.sentiment_history)  # oldest-first == ascending here


def test_portfolio_summary_folds_per_company_correctly():
    from app.engine.summary import summarize_portfolio

    a = summarize(
        [_signal(SignalType.NEW_RISK_FACTOR, 0.5, NOW)],
        [_signal(SignalType.SENTIMENT_SHIFT, 0.5, NOW, sentiment=0.4)],
    )
    b = summarize(
        [_signal(SignalType.NEW_RISK_FACTOR, 0.5, NOW), _signal(SignalType.NOTABLE_QUOTE, 0.5, NOW)],
        [_signal(SignalType.SENTIMENT_SHIFT, 0.5, NOW, sentiment=-0.2)],
    )
    result = summarize_portfolio([("A", a), ("B", b)])

    assert result.companies_total == 2
    assert result.companies_covered == 2
    assert result.total_risk_count == 2  # 1 from A + 1 from B
    assert round(result.avg_sentiment, 2) == round((0.4 + -0.2) / 2, 2)
    assert result.most_active_ticker == "B"
    assert result.most_active_signal_count == 2


def test_portfolio_summary_handles_no_companies():
    from app.engine.summary import summarize_portfolio

    result = summarize_portfolio([])
    assert result.companies_total == 0
    assert result.avg_sentiment is None
    assert result.most_active_ticker is None
