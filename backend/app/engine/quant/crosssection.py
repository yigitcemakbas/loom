"""Ranking one company against the rest of the universe.

A factor value on its own is close to unreadable. Accruals of 0.04 is a number
a professional has a feel for and nobody else does, and the feel is what
carries the judgement. Converting to a rank against every other company Loom
tracks replaces that private intuition with something the data states directly:
this company is in the worst tenth of the universe on the quality of its
earnings.

**Ranks rather than z-scores, deliberately.** These distributions are skewed
and have genuine outliers, a company that tripled its assets through an
acquisition is not a data error, and a z-score lets that one observation
dominate the scale for everyone else. A rank cannot: the extreme company lands
at the end of the line and the other hundred keep their spacing.

Ties take the average of the positions they span, which is the standard
treatment and matters here because several factors are near-constant across
most of the universe.
"""

from dataclasses import dataclass
from typing import Optional

# Below this many companies a percentile is not reported at all. Set to match
# the smallest peer group the sector layer will host (sectors/MIN_SECTOR_MEMBERS),
# because a rank inside a pool of eight banks is a real if coarse statement
# while a rank inside a pool of three is noise with a decimal point on it.
MIN_UNIVERSE = 8


@dataclass(frozen=True)
class Ranked:
    key: str
    value: float
    # 0.0 (worst in the universe) to 1.0 (best), already oriented so that high
    # is good for every factor regardless of the raw number's direction.
    percentile: float
    universe_size: int

    @property
    def is_extreme(self) -> bool:
        """Top or bottom decile. The readings worth putting in front of someone."""
        return self.percentile <= 0.1 or self.percentile >= 0.9


def _average_ranks(values: list[float]) -> list[float]:
    """Positions 0..n-1, with tied values sharing the mean of their span."""
    order = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
            j += 1
        shared = (i + j) / 2
        for k in range(i, j + 1):
            ranks[order[k]] = shared
        i = j + 1
    return ranks


def rank_universe(
    values: dict[str, float], *, higher_is_better: bool, key: str = ""
) -> dict[str, Ranked]:
    """Percentile-rank every company that has a value for one factor.

    Companies missing the factor are absent from both the input and the output.
    Imputing a median for them would be the conventional move and it is wrong
    here: a company that does not report gross profit is not an average-quality
    company, it is an unmeasured one, and the interface has to be able to say so.
    """
    if len(values) < MIN_UNIVERSE:
        return {}

    tickers = list(values)
    raw = [values[t] for t in tickers]
    ranks = _average_ranks(raw)
    # n-1 in the denominator so the ends of the distribution reach 0.0 and 1.0
    # rather than asymptotically approaching them.
    divisor = max(len(tickers) - 1, 1)

    out: dict[str, Ranked] = {}
    for ticker, rank in zip(tickers, ranks):
        percentile = rank / divisor
        if not higher_is_better:
            percentile = 1.0 - percentile
        out[ticker] = Ranked(
            key=key,
            value=values[ticker],
            percentile=round(percentile, 4),
            universe_size=len(tickers),
        )
    return out


def percentile_phrase(percentile: Optional[float]) -> Optional[str]:
    """Plain words for a rank, for a reader who does not think in percentiles.

    Deliberately coarse. Reporting "the 73rd percentile" implies the difference
    between 73 and 68 means something, and across a hundred and twenty
    companies on noisy accounting data it does not.
    """
    if percentile is None:
        return None
    if percentile >= 0.9:
        return "among the best in Loom's universe"
    if percentile >= 0.7:
        return "better than most companies Loom tracks"
    if percentile >= 0.3:
        return "middle of the pack"
    if percentile >= 0.1:
        return "worse than most companies Loom tracks"
    return "among the worst in Loom's universe"


__all__ = ["MIN_UNIVERSE", "Ranked", "percentile_phrase", "rank_universe"]
