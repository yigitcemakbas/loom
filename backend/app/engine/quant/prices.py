"""Point-in-time access to a company's price history.

The counterpart to series.py, and it exists for the same reason: a valuation
computed from tomorrow's price is not a valuation. The guard is simpler here
than for filings because a close is knowable the moment the session ends, with
no filing lag and no restatement, but it is easy to get wrong in one specific
way. Asking for "the price on 30 June" when 30 June was a Saturday must return
Friday's close, never Monday's, and a naive nearest-match returns whichever is
closer in days.

Two closes, used for different jobs and never interchangeably:

  close           what the share actually traded at, which is the number that
                  pairs with a share count to give a market capitalisation
  adjusted_close  restated for splits and dividends, the only honest basis for
                  measuring a return across time

A market capitalisation built from adjusted closes is wrong by every split the
company has ever done, and a return measured from raw closes reads a two-for-one
split as a fifty percent crash. Both errors look like findings.
"""

import bisect
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Optional

# How far back a lookup may reach for a session. Covers weekends and the
# longest exchange holiday runs; beyond it the series has a genuine hole and
# saying so is better than answering from a fortnight ago.
MAX_LOOKBACK_DAYS = 7


@dataclass(frozen=True)
class Bar:
    session_date: date
    close: float
    adjusted_close: float


class PriceHistory:
    """One company's daily closes, queried as at a date."""

    def __init__(self, bars: Iterable[Bar]):
        self._bars = sorted(bars, key=lambda b: b.session_date)
        self._dates = [b.session_date for b in self._bars]

    def __len__(self) -> int:
        return len(self._bars)

    def on_or_before(
        self, when: date, *, max_lookback_days: int = MAX_LOOKBACK_DAYS
    ) -> Optional[Bar]:
        """The last session that had closed by `when`.

        Strictly backwards. A nearest-match would return Monday's close for a
        Saturday, which is tomorrow's information wearing today's date, and it
        is the entire failure mode this class exists to prevent.
        """
        index = bisect.bisect_right(self._dates, when) - 1
        if index < 0:
            return None
        bar = self._bars[index]
        if (when - bar.session_date).days > max_lookback_days:
            return None
        return bar

    def close_on(self, when: date) -> Optional[float]:
        bar = self.on_or_before(when)
        return bar.close if bar else None

    def total_return(self, start: date, end: date) -> Optional[float]:
        """Return between two dates, split and dividend adjusted.

        Adjusted closes on both ends, always. Measuring this from raw closes
        reads a two-for-one split as a fifty percent crash, and the result is
        indistinguishable from a real one.
        """
        first = self.on_or_before(start)
        last = self.on_or_before(end)
        if first is None or last is None:
            return None
        if first.adjusted_close <= 0 or first.session_date >= last.session_date:
            return None
        return last.adjusted_close / first.adjusted_close - 1.0

    def daily_returns(self, start: date, end: date) -> list[float]:
        """Session-over-session returns inside a window, for measuring volatility."""
        window = [b for b in self._bars if start <= b.session_date <= end]
        out: list[float] = []
        for previous, current in zip(window, window[1:]):
            if previous.adjusted_close > 0:
                out.append(current.adjusted_close / previous.adjusted_close - 1.0)
        return out

    def covers(self, when: date, *, back_days: int) -> bool:
        """Whether the series actually reaches back far enough to answer.

        A momentum factor computed from four months of history for a company
        that listed last year is not a weak reading, it is a different
        measurement, and it would be ranked against companies with the full
        window as though it were the same thing.
        """
        if not self._bars:
            return False
        earliest = self.on_or_before(when - timedelta(days=back_days))
        return earliest is not None

    @property
    def newest(self) -> Optional[date]:
        return self._dates[-1] if self._dates else None


def history_from_bars(rows: Iterable) -> PriceHistory:
    """Adapt stored rows into the plain form this layer reasons over.

    The one place allowed to read a PriceBar, mirroring the boundary series.py
    keeps for filings and features.py keeps for signals.
    """
    return PriceHistory(
        Bar(
            session_date=row.session_date,
            close=float(row.close),
            adjusted_close=float(row.adjusted_close),
        )
        for row in rows
    )


__all__ = ["MAX_LOOKBACK_DAYS", "Bar", "PriceHistory", "history_from_bars"]
