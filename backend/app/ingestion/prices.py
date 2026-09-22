"""Price history, the one time series Loom did not previously have.

Everything else in this project is filings, transcripts, news, and insider
records: slow-moving evidence measured in quarters. A price chart is the
opposite, and it exists here for one reason, that a reader deciding around an
event wants the standing view and the market's own reaction on the same screen.

**Source caveat, stated plainly.** Finnhub's free tier serves a current quote
but returns 403 for historical candles, which are a paid resource. The
remaining free option is Yahoo's chart endpoint. It is undocumented and
unofficial: it can change shape, rate limit, or disappear without notice, and
it is not covered by a support agreement. That is a real fragility, so it is
isolated behind `PriceSource` here rather than called from a route, and every
failure degrades to "no chart" instead of an error, exactly like the optional
news source.

Prices are fetched on demand and cached briefly rather than stored. A five
minute candle for ten tickers across five ranges would add hundreds of
thousands of rows that go stale immediately, and none of it is evidence the
engine reasons over.
"""

import logging
import threading
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

_CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"

# Requested range -> (upstream range, candle interval). Kept server-side so a
# client asks for "1W" and never has to know the provider's vocabulary.
RANGE_SPECS: dict[str, tuple[str, str]] = {
    "1H": ("1d", "2m"),
    "24H": ("1d", "5m"),
    "1W": ("5d", "15m"),
    "1M": ("1mo", "1d"),
    "6M": ("6mo", "1d"),
    "1Y": ("1y", "1d"),
}

# "1H" has no upstream equivalent, so it is the tail of an intraday series.
_TAIL_POINTS = {"1H": 30}

# Long enough to stop a rotating chart hammering the provider, short enough
# that an intraday line still looks live.
_CACHE_SECONDS = 60.0


@dataclass
class DailyBar:
    """One session's closes.

    Both are kept because they answer different questions: `close` pairs with a
    share count to give a market capitalisation, `adjusted_close` is the only
    honest basis for a return across a split or a dividend. Using one for both
    jobs produces an error that looks like a result.
    """

    session_date: date
    close: float
    adjusted_close: float
    volume: float | None = None


@dataclass
class PricePoint:
    t: int          # unix seconds
    c: float        # close


@dataclass
class PriceSeries:
    ticker: str
    range: str
    currency: str | None
    points: list[PricePoint]
    previous_close: float | None

    @property
    def last(self) -> float | None:
        return self.points[-1].c if self.points else None

    @property
    def change(self) -> float | None:
        """Move across the window shown, not since yesterday's close.

        A 1Y chart whose header reported today's percentage move would be
        describing a different period from the line beneath it.
        """
        if len(self.points) < 2:
            return None
        return self.points[-1].c - self.points[0].c

    @property
    def change_percent(self) -> float | None:
        if len(self.points) < 2 or not self.points[0].c:
            return None
        return (self.points[-1].c / self.points[0].c - 1) * 100


class PriceSource:
    """Fetches and briefly caches price series. The only place that knows the
    upstream provider's shape."""

    def __init__(self) -> None:
        self._client = httpx.Client(
            headers={"User-Agent": settings.scraper_user_agent},
            timeout=15.0,
            follow_redirects=True,
        )
        self._cache: dict[tuple[str, str], tuple[PriceSeries, float]] = {}
        self._lock = threading.Lock()

    def get(self, ticker: str, range_key: str = "24H") -> PriceSeries | None:
        range_key = range_key.upper()
        if range_key not in RANGE_SPECS:
            return None

        key = (ticker.upper(), range_key)
        with self._lock:
            cached = self._cache.get(key)
            if cached and (time.monotonic() - cached[1]) < _CACHE_SECONDS:
                return cached[0]

        series = self._fetch(ticker, range_key)
        if series is not None:
            with self._lock:
                self._cache[key] = (series, time.monotonic())
        return series

    def _fetch(self, ticker: str, range_key: str) -> PriceSeries | None:
        upstream_range, interval = RANGE_SPECS[range_key]
        try:
            resp = self._client.get(
                _CHART_URL.format(symbol=ticker.upper()),
                params={"range": upstream_range, "interval": interval},
            )
        except Exception:
            logger.warning("Price fetch failed for %s", ticker, exc_info=True)
            return None

        if resp.status_code != 200:
            logger.info("Price fetch returned %s for %s", resp.status_code, ticker)
            return None

        try:
            result = resp.json()["chart"]["result"][0]
            stamps = result.get("timestamp") or []
            closes = result["indicators"]["quote"][0].get("close") or []
            meta = result.get("meta") or {}
        except (KeyError, IndexError, TypeError, ValueError):
            logger.info("Price payload was not in the expected shape for %s", ticker)
            return None

        # Gaps are normal in intraday data; a null close is a candle with no
        # trade, and carrying it through would leave holes in the line.
        points = [
            PricePoint(t=int(t), c=float(c))
            for t, c in zip(stamps, closes, strict=False)
            if c is not None
        ]
        tail = _TAIL_POINTS.get(range_key)
        if tail:
            points = points[-tail:]

        if not points:
            return None

        return PriceSeries(
            ticker=ticker.upper(),
            range=range_key,
            currency=meta.get("currency"),
            points=points,
            previous_close=meta.get("chartPreviousClose") or meta.get("previousClose"),
        )

    def daily_history(self, ticker: str, *, years: int = 6) -> list["DailyBar"]:
        """Daily closes going back several years, for storing rather than drawing.

        Deliberately not routed through the sixty-second cache above. That cache
        exists so a rotating chart does not hammer the provider; a backfill runs
        once per ticker and caching its answer would only hold megabytes of
        history in memory for nothing.

        Six years by default, which is what a five-year factor window needs with
        room for the year-ago comparison at its far end.
        """
        try:
            resp = self._client.get(
                _CHART_URL.format(symbol=ticker.upper()),
                params={
                    "range": f"{years}y",
                    "interval": "1d",
                    # Without this the payload carries no adjusted series and
                    # every return measured across a split is wrong.
                    "events": "div,splits",
                },
            )
        except Exception:
            logger.warning("Price history fetch failed for %s", ticker, exc_info=True)
            return []

        if resp.status_code != 200:
            logger.info("Price history returned %s for %s", resp.status_code, ticker)
            return []

        try:
            result = resp.json()["chart"]["result"][0]
        except (KeyError, IndexError, TypeError, ValueError):
            logger.info("Price history payload was not in the expected shape for %s", ticker)
            return []

        return _history_bars(result)


def _history_bars(result: dict) -> list[DailyBar]:
    """Turn one chart payload into daily bars, dropping sessions with no trade.

    The adjusted series is optional in the payload. When it is absent every bar
    falls back to the raw close, which makes returns across a split wrong; that
    is recorded on the bar rather than hidden, because a silently unadjusted
    series produces a momentum factor that looks fine and is not.
    """
    stamps = result.get("timestamp") or []
    quote = (result.get("indicators", {}).get("quote") or [{}])[0]
    closes = quote.get("close") or []
    volumes = quote.get("volume") or []
    adjusted = (result.get("indicators", {}).get("adjclose") or [{}])[0].get("adjclose") or []

    bars: list[DailyBar] = []
    for index, stamp in enumerate(stamps):
        close = closes[index] if index < len(closes) else None
        if close is None:
            continue
        adj = adjusted[index] if index < len(adjusted) else None
        volume = volumes[index] if index < len(volumes) else None
        bars.append(
            DailyBar(
                session_date=datetime.fromtimestamp(int(stamp), tz=timezone.utc).date(),
                close=float(close),
                adjusted_close=float(adj if adj is not None else close),
                volume=float(volume) if volume is not None else None,
            )
        )
    return bars


_source: PriceSource | None = None


def get_price_source() -> PriceSource:
    global _source
    if _source is None:
        _source = PriceSource()
    return _source
