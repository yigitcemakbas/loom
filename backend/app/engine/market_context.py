"""What the market has already done, alongside what the filings said.

This module exists because of an error worth recording. The project's founding
constraint was "not a numeric price predictor", meaning no forecasting model.
That was read far too broadly as "do not use price data", with the result that
the engine could describe margin pressure in detail while having no idea the
stock had already fallen twenty percent on it. Price was fetched for charts and
never reached the analysis.

Those are different things. Refusing to forecast a price is a position about
what this system can honestly claim. Refusing to *look* at the price throws
away the single most important piece of context a finding has: whether the
market already knows.

Everything here is arithmetic over a price series. It states what happened. It
does not extrapolate, and nothing in it produces a recommendation.
"""

import logging
from dataclasses import dataclass
from datetime import datetime, timezone

from app.ingestion.prices import PriceSeries, get_price_source

logger = logging.getLogger(__name__)

# Trading days, not calendar days: the series only contains sessions.
_WINDOWS = {"1w": 5, "1m": 21, "3m": 63, "6m": 126, "1y": 252}

# A finding is judged against the market's reaction over this many sessions
# after it was disclosed. Roughly two trading weeks, long enough for a reaction
# to resolve and short enough to still be attributable.
REACTION_SESSIONS = 10

# Below this the position in the 52 week range is not meaningful.
_MIN_POINTS = 20


@dataclass
class MarketContext:
    """Where the price stands, and how it has moved."""

    last: float
    currency: str | None
    change_1w: float | None
    change_1m: float | None
    change_3m: float | None
    change_6m: float | None
    change_1y: float | None
    low_52w: float | None
    high_52w: float | None
    # 0.0 at the 52 week low, 1.0 at the high. The single most compact answer
    # to "is this thing beaten down or priced for perfection".
    position_in_range: float | None
    summary: str


@dataclass
class PriceReaction:
    """How the price moved after a finding was disclosed."""

    change_percent: float
    sessions: int
    already_moved: bool
    summary: str


# A window may be computed from slightly less history than requested: a
# provider's "1 year" range returns 251 sessions where a trading year is ~252,
# which silently made the one year change permanently uncomputable. Allow a
# short shortfall, and refuse anything materially shorter rather than labelling
# three months of data as a year.
_MIN_WINDOW_COVERAGE = 0.9


def _pct(series: list[float], sessions: int) -> float | None:
    available = len(series) - 1
    if available < 1:
        return None

    span = min(sessions, available)
    if span < sessions * _MIN_WINDOW_COVERAGE:
        return None

    then, now = series[-(span + 1)], series[-1]
    if not then:
        return None
    return round((now / then - 1) * 100, 2)


def _describe(last: float, changes: dict[str, float | None], position: float | None) -> str:
    """One plain sentence. No forecast, only what has happened."""
    parts: list[str] = []

    month = changes.get("1m")
    year = changes.get("1y")
    if month is not None:
        direction = "up" if month >= 0 else "down"
        parts.append(f"{direction} {abs(month):.1f}% over the past month")
    if year is not None:
        direction = "up" if year >= 0 else "down"
        parts.append(f"{direction} {abs(year):.1f}% over the year")

    where = ""
    if position is not None:
        if position >= 0.9:
            where = ", trading near its 52 week high"
        elif position <= 0.1:
            where = ", trading near its 52 week low"
        else:
            where = f", about {position * 100:.0f}% of the way up its 52 week range"

    if not parts:
        return f"Last traded at {last:.2f}."
    return f"Last traded at {last:.2f}, {' and '.join(parts)}{where}."


def build_context(series: PriceSeries | None) -> MarketContext | None:
    """Fold a one-year daily series into the standing price picture."""
    if series is None or len(series.points) < 2:
        return None

    closes = [p.c for p in series.points]
    last = closes[-1]
    changes = {name: _pct(closes, n) for name, n in _WINDOWS.items()}

    low = high = position = None
    if len(closes) >= _MIN_POINTS:
        window = closes[-_WINDOWS["1y"]:]
        low, high = min(window), max(window)
        span = high - low
        position = round((last - low) / span, 3) if span else None

    return MarketContext(
        last=round(last, 2),
        currency=series.currency,
        change_1w=changes["1w"],
        change_1m=changes["1m"],
        change_3m=changes["3m"],
        change_6m=changes["6m"],
        change_1y=changes["1y"],
        low_52w=round(low, 2) if low is not None else None,
        high_52w=round(high, 2) if high is not None else None,
        position_in_range=position,
        summary=_describe(last, changes, position),
    )


def reaction_since(series: PriceSeries | None, occurred_at: datetime) -> PriceReaction | None:
    """How the price moved in the sessions after a finding was disclosed.

    This is the context that decides whether a finding is news or history. A
    risk disclosed three weeks ago, after which the stock fell twelve percent,
    is largely reflected in the price. The same risk disclosed yesterday, with
    the price unmoved, is not.
    """
    if series is None or len(series.points) < 2:
        return None

    when = occurred_at if occurred_at.tzinfo else occurred_at.replace(tzinfo=timezone.utc)
    cutoff = when.timestamp()

    at_or_after = [p for p in series.points if p.t >= cutoff]
    if len(at_or_after) < 2:
        return None

    start = at_or_after[0].c
    window = at_or_after[: REACTION_SESSIONS + 1]
    end = window[-1].c
    if not start:
        return None

    change = round((end / start - 1) * 100, 2)
    sessions = len(window) - 1

    # "Already moved" is a deliberately blunt threshold. It answers whether the
    # market has visibly responded, not whether it responded correctly or by
    # enough, neither of which this system can judge.
    already_moved = abs(change) >= 5.0

    direction = "fell" if change < 0 else "rose"
    summary = (
        f"The price {direction} {abs(change):.1f}% over the {sessions} sessions after this, "
        f"so the market has {'already reacted' if already_moved else 'barely moved'}."
    )

    return PriceReaction(
        change_percent=change,
        sessions=sessions,
        already_moved=already_moved,
        summary=summary,
    )


def context_for(ticker: str) -> MarketContext | None:
    """Convenience wrapper. Returns None rather than raising when prices are
    unavailable, so a missing series costs the price panel and nothing else."""
    try:
        return build_context(get_price_source().get(ticker, "1Y"))
    except Exception:
        logger.warning("Market context unavailable for %s", ticker, exc_info=True)
        return None
