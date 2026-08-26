"""Per-company rollups for the dashboard.

This is the synthesis layer: it turns a pile of individual signals into the
single "what does this company's situation look like right now" read the
dashboard needs. No LLM here, everything is deterministic aggregation over
signals the extraction/diffing stages already produced. This is what makes
the dashboard a cockpit instead of a document list: every row is a computed
result, not a raw record.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from app.models.signal import Signal, SignalType

# How far back "recent activity" counts for the dashboard's risk/signal
# counters. Long enough to span a quarter's filings, short enough that a
# stale finding from two years ago doesn't inflate today's count.
WINDOW_DAYS = 90

# How many points the dashboard's sparkline shows. A trend arrow alone throws
# away the shape of a company's sentiment history; a short series recovers it
# without turning the row into a full chart.
SPARKLINE_POINTS = 8

_RISK_TYPES = (SignalType.NEW_RISK_FACTOR, SignalType.QOQ_ANOMALY)


@dataclass
class CompanySummary:
    sentiment_score: float | None       # most recent reading, -1..1
    sentiment_trend: float | None       # change vs the prior reading; None if <2 points exist
    sentiment_history: list[float]      # oldest-first, up to SPARKLINE_POINTS readings
    signal_count: int                   # signals in the recent window
    risk_count: int                     # of those, the ones that are risk findings
    top_signal: Signal | None           # single highest-priority signal, any age
    last_signal_at: datetime | None

    # A screener row is only as useful as the number of independent columns it
    # can be sorted and compared on. These are all folds over signals already
    # loaded for the counters above, so they cost no extra query.
    bearish_count: int                  # recent findings assessed negative
    bullish_count: int                  # recent findings assessed positive
    major_count: int                    # recent findings assessed "major"
    pattern_count: int                  # cross-document syntheses, the rarest and strongest type
    insider_net_usd: float              # open-market buys minus sells, from Form 4 facts
    top_priority: float                 # rank of the strongest finding, for cross-company ordering
    avg_confidence: float | None        # mean confidence of recent findings


@dataclass
class PortfolioSummary:
    """The strip above the per-company table, a read on the whole watchlist,
    not any one row. Folded from the same CompanySummary objects the route
    already builds; no separate query."""

    companies_total: int
    companies_covered: int          # have at least one signal
    total_risk_count: int
    avg_sentiment: float | None     # mean over companies with a reading
    trend_up: int
    trend_down: int
    most_active_ticker: str | None
    most_active_signal_count: int


def summarize(
    all_signals: list[Signal],
    sentiment_series: list[Signal],
    facts: list | None = None,
) -> CompanySummary:
    """Build one company's rollup.

    `all_signals` drives the top finding and recent-activity counters;
    `sentiment_series` is oldest-first (as SignalRepository.sentiment_series
    returns it) and drives the trend line; `facts` is optional structured data
    (Phase 5) used for the insider column, absent for a company with none.
    """
    cutoff = datetime.now(timezone.utc) - timedelta(days=WINDOW_DAYS)
    recent = [s for s in all_signals if _aware(s.occurred_at) >= cutoff]
    risk_count = sum(1 for s in recent if s.signal_type in _RISK_TYPES)

    top = max(all_signals, key=lambda s: s.priority, default=None)

    confidences = [s.confidence for s in recent if s.confidence is not None]

    trend = None
    if len(sentiment_series) >= 2:
        trend = sentiment_series[-1].sentiment_score - sentiment_series[-2].sentiment_score
    current_sentiment = sentiment_series[-1].sentiment_score if sentiment_series else None
    history = [s.sentiment_score for s in sentiment_series[-SPARKLINE_POINTS:] if s.sentiment_score is not None]

    last_at = max((s.occurred_at for s in all_signals), default=None)

    return CompanySummary(
        sentiment_score=current_sentiment,
        sentiment_trend=trend,
        sentiment_history=history,
        signal_count=len(recent),
        risk_count=risk_count,
        top_signal=top,
        last_signal_at=last_at,
        bearish_count=sum(1 for s in recent if s.market_direction == "negative"),
        bullish_count=sum(1 for s in recent if s.market_direction == "positive"),
        major_count=sum(1 for s in recent if s.market_magnitude == "major"),
        pattern_count=sum(1 for s in recent if s.signal_type == SignalType.EMERGING_PATTERN),
        insider_net_usd=_insider_net_usd(facts or []),
        top_priority=top.priority if top else 0.0,
        avg_confidence=sum(confidences) / len(confidences) if confidences else None,
    )


def _insider_net_usd(facts: list) -> float:
    """Open-market buys minus sells, in dollars.

    Only open-market trades count. The rest of a Form 4 is vesting and option
    mechanics, which would swamp this number with activity nobody decided on
    (see engine/fact_rules.py).
    """
    net = 0.0
    for fact in facts:
        attributes = getattr(fact, "attributes", None) or {}
        if not attributes.get("is_open_market"):
            continue
        value = attributes.get("value_usd")
        if not value:
            continue
        net += -float(value) if attributes.get("disposed") else float(value)
    return round(net, 2)


def summarize_portfolio(summaries: list[tuple[str, CompanySummary]]) -> PortfolioSummary:
    """Fold per-company summaries into one portfolio-level read."""
    covered = [(t, s) for t, s in summaries if s.sentiment_score is not None]
    sentiments = [s.sentiment_score for _, s in covered]

    most_active_ticker = None
    most_active_count = 0
    if summaries:
        most_active_ticker, most_active_summary = max(summaries, key=lambda pair: pair[1].signal_count)
        most_active_count = most_active_summary.signal_count

    return PortfolioSummary(
        companies_total=len(summaries),
        companies_covered=len(covered),
        total_risk_count=sum(s.risk_count for _, s in summaries),
        avg_sentiment=sum(sentiments) / len(sentiments) if sentiments else None,
        trend_up=sum(1 for _, s in summaries if s.sentiment_trend is not None and s.sentiment_trend > 0.03),
        trend_down=sum(1 for _, s in summaries if s.sentiment_trend is not None and s.sentiment_trend < -0.03),
        most_active_ticker=most_active_ticker,
        most_active_signal_count=most_active_count,
    )


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
