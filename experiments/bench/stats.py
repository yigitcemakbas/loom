"""Statistics, in pure Python, because this machine has no numpy.

Two conventions are load-bearing and both come from a mistake already made in
this project. A statistic that cannot be computed returns None, never zero: a
zero t-statistic reads as "measured, no effect" and an absent one means "not
measured", and collapsing them is how a null becomes a finding. And overlapping
windows are never fed to a plain t-test, because the same quarter appearing in
three observations triples the apparent sample without adding information.
"""
from __future__ import annotations

import math
import random
from typing import Callable, Optional, Sequence


def mean(xs: Sequence[float]) -> Optional[float]:
    return sum(xs) / len(xs) if xs else None


def stdev(xs: Sequence[float]) -> Optional[float]:
    if len(xs) < 2:
        return None
    m = sum(xs) / len(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def ranks(xs: Sequence[float]) -> list[float]:
    """Average ranks, ties shared. Ties matter here: factor percentiles arrive
    already bucketed and a naive rank would invent an ordering inside a tie."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    out = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        shared = (i + j) / 2 + 1
        for k in range(i, j + 1):
            out[order[k]] = shared
        i = j + 1
    return out


def pearson(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    n = len(xs)
    if n < 3 or n != len(ys):
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    sx = math.sqrt(sum((x - mx) ** 2 for x in xs))
    sy = math.sqrt(sum((y - my) ** 2 for y in ys))
    if sx == 0 or sy == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / (sx * sy)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    if len(xs) < 3 or len(xs) != len(ys):
        return None
    return pearson(ranks(xs), ranks(ys))


# ------------------------------------------------------- the t distribution


def _betacf(a: float, b: float, x: float) -> float:
    """Continued fraction for the incomplete beta function (Lentz's method)."""
    tiny = 1e-30
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    if abs(d) < tiny:
        d = tiny
    d = 1.0 / d
    h = d
    for m in range(1, 200):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        if abs(d) < tiny:
            d = tiny
        c = 1.0 + aa / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-12:
            break
    return h


def _betai(a: float, b: float, x: float) -> float:
    if x <= 0.0:
        return 0.0
    if x >= 1.0:
        return 1.0
    lbeta = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
             + a * math.log(x) + b * math.log(1.0 - x))
    front = math.exp(lbeta)
    if x < (a + 1.0) / (a + b + 2.0):
        return front * _betacf(a, b, x) / a
    return 1.0 - math.exp(
        math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
        + b * math.log(1.0 - x) + a * math.log(x)) * _betacf(b, a, 1.0 - x) / b


def two_sided_p(t: Optional[float], df: int) -> Optional[float]:
    """An exact Student-t p-value. A normal approximation was the alternative
    and it overstates significance at the sample sizes here."""
    if t is None or df < 1:
        return None
    return _betai(df / 2.0, 0.5, df / (df + t * t))


def t_test_mean(xs: Sequence[float]) -> tuple[Optional[float], Optional[float]]:
    """Is the mean different from zero. Returns (t, p), both None when the
    series has no variance rather than a spurious zero."""
    if len(xs) < 2:
        return None, None
    s = stdev(xs)
    if s is None or s == 0:
        return None, None
    t = (sum(xs) / len(xs)) / (s / math.sqrt(len(xs)))
    return t, two_sided_p(t, len(xs) - 1)


def newey_west_t(xs: Sequence[float], lags: int) -> tuple[Optional[float], Optional[float]]:
    """A t-statistic robust to the autocorrelation that overlapping holding
    periods guarantee. Used wherever the observation windows overlap, which is
    everywhere the horizon is longer than the rebalance interval."""
    n = len(xs)
    if n < 3:
        return None, None
    m = sum(xs) / n
    e = [x - m for x in xs]
    gamma0 = sum(v * v for v in e) / n
    var = gamma0
    for l in range(1, min(lags, n - 1) + 1):
        g = sum(e[i] * e[i - l] for i in range(l, n)) / n
        var += 2 * (1 - l / (lags + 1)) * g
    if var <= 0:
        return None, None
    t = m / math.sqrt(var / n)
    return t, two_sided_p(t, n - 1)


# ------------------------------------------------------- resampling


def block_bootstrap_ci(xs: Sequence[float], *, stat: Callable[[Sequence[float]], Optional[float]] = mean,
                       resamples: int = 10_000, block: int = 5,
                       confidence: float = 0.95, seed: int = 7
                       ) -> tuple[Optional[float], Optional[float], Optional[float]]:
    """A stationary block bootstrap interval: (point, low, high).

    Blocks rather than single draws because the series is serially dependent,
    and stationary (geometric block lengths) rather than fixed so the interval
    does not depend on where the blocks happen to start.
    """
    if len(xs) < 3:
        return (stat(xs) if xs else None), None, None
    point = stat(xs)
    rnd = random.Random(seed)
    n = len(xs)
    p = 1.0 / block
    draws: list[float] = []
    for _ in range(resamples):
        sample: list[float] = []
        i = rnd.randrange(n)
        while len(sample) < n:
            sample.append(xs[i])
            i = (i + 1) % n if rnd.random() > p else rnd.randrange(n)
        v = stat(sample)
        if v is not None:
            draws.append(v)
    if not draws:
        return point, None, None
    draws.sort()
    lo = draws[int((1 - confidence) / 2 * len(draws))]
    hi = draws[min(len(draws) - 1, int((1 + confidence) / 2 * len(draws)))]
    return point, lo, hi


def benjamini_hochberg(pvals: dict[str, Optional[float]], alpha: float = 0.05
                       ) -> dict[str, tuple[Optional[float], bool]]:
    """Adjusted p-values and which survive, over a declared family.

    Necessary rather than optional: this benchmark makes on the order of thirty
    comparisons, and at alpha 0.05 roughly one and a half of them are expected
    to look significant with no effect present at all.
    """
    named = [(k, v) for k, v in pvals.items() if v is not None]
    named.sort(key=lambda kv: kv[1])
    m = len(named)
    out: dict[str, tuple[Optional[float], bool]] = {k: (None, False) for k in pvals}
    running = 1.0
    for i in range(m - 1, -1, -1):
        k, p = named[i]
        running = min(running, p * m / (i + 1))
        out[k] = (min(1.0, running), running <= alpha)
    return out


# ------------------------------------------------------- portfolio measures


def max_drawdown(series: Sequence[float]) -> float:
    """Worst peak-to-trough on a compounded path of period returns."""
    peak, worst, level = 1.0, 0.0, 1.0
    for r in series:
        level *= (1 + r)
        peak = max(peak, level)
        worst = min(worst, level / peak - 1)
    return worst


def annualised(series: Sequence[float], periods_per_year: int) -> Optional[float]:
    s = stdev(series)
    return s * math.sqrt(periods_per_year) if s is not None else None


def downside_deviation(series: Sequence[float], target: float = 0.0) -> Optional[float]:
    below = [min(0.0, r - target) for r in series]
    if len(below) < 2:
        return None
    return math.sqrt(sum(b * b for b in below) / (len(below) - 1))


def sharpe(series: Sequence[float], periods_per_year: int) -> Optional[float]:
    s = stdev(series)
    if not s:
        return None
    return (sum(series) / len(series)) / s * math.sqrt(periods_per_year)


def sortino(series: Sequence[float], periods_per_year: int) -> Optional[float]:
    d = downside_deviation(series)
    if not d:
        return None
    return (sum(series) / len(series)) / d * math.sqrt(periods_per_year)


def hhi(weights: Sequence[float]) -> Optional[float]:
    total = sum(abs(w) for w in weights)
    if total <= 0:
        return None
    return sum((abs(w) / total) ** 2 for w in weights)
