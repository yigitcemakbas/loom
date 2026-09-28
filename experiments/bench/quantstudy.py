"""The mechanical benchmark: does Loom's ranking carry information, and where.

This half of the experiment needs no language model, which is why it carries the
weight. Loom's document layer covers about forty companies since October 2025;
its quantitative layer covers nine hundred back to 2020 and can therefore be
tested across the COVID collapse, the 2022 bear market and two bull stretches,
on eighty monthly cross-sections instead of three overlapping quarters.

What it can answer: whether the ranking predicts returns, which factor does the
work, whether the effect survives regimes, sectors and size strata, whether
Loom's percentiles are calibrated, and whether the benefit comes from choosing
names or from sizing them.

What it cannot answer: anything about a reader. A portfolio formed
mechanically from Loom's output is not a person using Loom, and the reader
questions are answered, at twenty model calls a day, in reader_run.py.
"""
from __future__ import annotations

import json
import math
import pathlib
import random
from collections import defaultdict
from datetime import date
from typing import Optional

from sqlalchemy import text as sqltext

import stats

HERE = pathlib.Path(__file__).parent
SCORES = HERE / "scores"
BENCHMARK = "QQQ"
HORIZONS = (21, 63, 126)
QUINTILE = 0.20
MIN_UNIVERSE = 100          # a cross-section thinner than this is not a cross-section
SESSIONS_PER_YEAR = 252


# --------------------------------------------------------------- price data


class Tape:
    """Every company's daily returns on one shared session calendar.

    The calendar is the benchmark's sessions, because the benchmark trades every
    day the market is open and any company can be missing. Alignment to a shared
    index is what makes a portfolio's daily path addable at all.
    """

    def __init__(self, db):
        rows = db.execute(sqltext("""
            select c.ticker, p.session_date, coalesce(p.adjusted_close, p.close) px
            from price_bars p join companies c on c.id = p.company_id
            where p.session_date >= '2019-06-01'
            order by p.session_date
        """)).all()
        by_ticker: dict[str, dict[date, float]] = defaultdict(dict)
        for r in rows:
            if r.px and r.px > 0:
                by_ticker[r.ticker][r.session_date] = float(r.px)

        self.calendar: list[date] = sorted(by_ticker.get(BENCHMARK, {}))
        self._index = {d: i for i, d in enumerate(self.calendar)}
        self.closes = by_ticker
        self.sectors: dict[str, str] = {
            r.ticker: (r.sector or "unclassified")
            for r in db.execute(sqltext("select ticker, sector from companies")).all()}
        self.rank: dict[str, Optional[int]] = {
            r.ticker: r.sec_rank
            for r in db.execute(sqltext("select ticker, sec_rank from companies")).all()}

    def position(self, d: date) -> Optional[int]:
        """Index of the last session at or before `d`. Strictly backwards."""
        lo, hi = 0, len(self.calendar) - 1
        if hi < 0 or d < self.calendar[0]:
            return None
        best = None
        while lo <= hi:
            mid = (lo + hi) // 2
            if self.calendar[mid] <= d:
                best, lo = mid, mid + 1
            else:
                hi = mid - 1
        return best

    def window(self, d: date, sessions: int) -> Optional[list[date]]:
        i = self.position(d)
        if i is None or i + sessions >= len(self.calendar):
            return None
        return self.calendar[i:i + sessions + 1]

    def path(self, ticker: str, win: list[date]) -> Optional[list[float]]:
        """Daily returns across the window, or None if the name is not quoted
        throughout. A partially quoted name is dropped rather than carried at
        zero, because a zero return is a claim that the price did not move."""
        closes = self.closes.get(ticker)
        if not closes:
            return None
        px = [closes.get(d) for d in win]
        if any(p is None for p in px):
            return None
        return [px[i] / px[i - 1] - 1 for i in range(1, len(px))]

    def total(self, ticker: str, win: list[date]) -> Optional[float]:
        closes = self.closes.get(ticker)
        if not closes:
            return None
        a, b = closes.get(win[0]), closes.get(win[-1])
        if not a or not b:
            return None
        return b / a - 1


# --------------------------------------------------------------- portfolios


def measure(weights: dict[str, float], tape: Tape, win: list[date],
            bench: float) -> Optional[dict]:
    """Every metric for one weighted book over one window.

    Weights are signed; the unallocated remainder is cash and earns nothing,
    which is the honest treatment for a benchmark that charges no financing.
    """
    paths = {t: tape.path(t, win) for t in weights}
    paths = {t: p for t, p in paths.items() if p}
    if not paths:
        return None
    n = len(win) - 1
    daily = [sum(weights[t] * paths[t][i] for t in paths) for i in range(n)]
    totals = {t: tape.total(t, win) for t in paths}
    gross = sum(abs(w) for w in weights.values())
    longs = [t for t, w in weights.items() if w > 0]
    wins = [1 for t in longs if (totals.get(t) or 0) > bench]
    return {
        "return": math.prod(1 + r for r in daily) - 1,
        "excess": math.prod(1 + r for r in daily) - 1 - bench,
        "volatility": stats.annualised(daily, SESSIONS_PER_YEAR),
        "max_drawdown": stats.max_drawdown(daily),
        "downside_deviation": stats.downside_deviation(daily),
        "sharpe": stats.sharpe(daily, SESSIONS_PER_YEAR),
        "sortino": stats.sortino(daily, SESSIONS_PER_YEAR),
        "hhi": stats.hhi(list(weights.values())),
        "max_weight": max((abs(w) for w in weights.values()), default=0.0),
        "sector_hhi": _sector_hhi(weights, tape),
        "n_names": len(paths),
        "gross": gross,
        "cash": max(0.0, 1 - gross),
        "hit_rate": (len(wins) / len(longs)) if longs else None,
        "daily": daily,
    }


def _sector_hhi(weights: dict[str, float], tape: Tape) -> Optional[float]:
    by_sector: dict[str, float] = defaultdict(float)
    for t, w in weights.items():
        by_sector[tape.sectors.get(t, "unclassified")] += abs(w)
    return stats.hhi(list(by_sector.values()))


def equal(names: list[str], gross: float = 1.0) -> dict[str, float]:
    return {t: gross / len(names) for t in names} if names else {}


def long_short(top: list[str], bottom: list[str]) -> dict[str, float]:
    w: dict[str, float] = {}
    if top:
        for t in top:
            w[t] = 0.5 / len(top)
    if bottom:
        for t in bottom:
            w[t] = w.get(t, 0.0) - 0.5 / len(bottom)
    return w


def sized(scores: dict[str, float], gross: float = 1.0) -> dict[str, float]:
    """Every name, weighted by how far its percentile sits from the middle.

    This is the sizing arm. It holds the same universe as the equal-weight
    control and differs only in how much of each, which is what isolates sizing
    from selection.
    """
    tilt = {t: (p - 0.5) for t, p in scores.items()}
    total = sum(abs(v) for v in tilt.values())
    if total == 0:
        return {}
    return {t: gross * v / total for t, v in tilt.items() if v}


# --------------------------------------------------------------- the study


def load_scores() -> list[dict]:
    out = []
    for p in sorted(SCORES.glob("*.json")):
        d = json.loads(p.read_text())
        if d.get("n", 0) >= MIN_UNIVERSE:
            d["date"] = date.fromisoformat(d["as_of"])
            out.append(d)
    return out


def factor_keys(snapshots: list[dict]) -> list[str]:
    seen: dict[str, int] = defaultdict(int)
    for s in snapshots:
        for pcts in s["percentiles"].values():
            for k in pcts:
                seen[k] += 1
    # Only factors present in most of the corpus. A factor computable on a
    # handful of companies produces a cross-section too thin to rank.
    threshold = 0.3 * sum(len(s["percentiles"]) for s in snapshots)
    return sorted(k for k, n in seen.items() if n >= threshold)


def run(db) -> dict:
    tape = Tape(db)
    snapshots = load_scores()
    keys = factor_keys(snapshots)
    results: dict = {"dates": len(snapshots), "factors": keys, "horizons": {}}

    for horizon in HORIZONS:
        ic_by_factor: dict[str, list[float]] = defaultdict(list)
        fm_slope: list[float] = []
        books: dict[str, list[dict]] = defaultdict(list)
        deciles: dict[int, list[float]] = defaultdict(list)
        regimes: list[dict] = []
        strata_ic: dict[str, list[float]] = defaultdict(list)
        sector_ic: dict[str, list[float]] = defaultdict(list)
        effects = {"missed_upside": [], "avoided_loss": [],
                   "captured_upside": [], "added_loss": []}

        for snap in snapshots:
            win = tape.window(snap["date"], horizon)
            if win is None:
                continue
            bench = tape.total(BENCHMARK, win)
            if bench is None:
                continue

            pcts = snap["percentiles"]
            fwd = {t: tape.total(t, win) for t in pcts}
            fwd = {t: v for t, v in fwd.items() if v is not None}
            if len(fwd) < MIN_UNIVERSE:
                continue
            excess = {t: fwd[t] - bench for t in fwd}

            # Information coefficient, per factor, this cross-section.
            for k in keys:
                xs = [(t, pcts[t][k]) for t in fwd if k in pcts[t]]
                if len(xs) < 30:
                    continue
                rho = stats.spearman([v for _, v in xs], [excess[t] for t, _ in xs])
                if rho is not None:
                    ic_by_factor[k].append(rho)

            # Both foldings are measured. `composite_weighted` is what Loom
            # shows a user and carries the primary claim; `composite_flat` is
            # the plain average of percentiles and exists to say whether the
            # theme weighting adds anything or only adds machinery.
            for label, book in (("composite_weighted", snap.get("composite_weighted") or {}),
                                ("composite_flat", snap.get("composite_flat") or {})):
                have = {t: v for t, v in book.items() if t in fwd}
                if len(have) >= MIN_UNIVERSE:
                    rho = stats.spearman([have[t] for t in have],
                                         [excess[t] for t in have])
                    if rho is not None:
                        ic_by_factor[label].append(rho)

            comp = {t: v for t, v in (snap.get("composite_weighted") or {}).items()
                    if t in fwd}
            if len(comp) < MIN_UNIVERSE:
                comp = {t: v for t, v in (snap.get("composite_flat") or {}).items()
                        if t in fwd}
            if len(comp) < MIN_UNIVERSE:
                continue

            # Fama-MacBeth: one cross-sectional slope per date, tested later as
            # a time series. The standard treatment, and the reason the result
            # is not one regression over pooled observations whose errors are
            # correlated within every date.
            slope = _ols_slope([comp[t] for t in comp], [excess[t] for t in comp])
            if slope is not None:
                fm_slope.append(slope)

            # Calibration: does a higher percentile actually pay more. Bucketed
            # by rank within the cross-section, not by the raw score. Value
            # bucketing was tried first and produced deciles holding 5 names and
            # 1,697 respectively, because a composite is an average of
            # percentiles and averaging concentrates it near the middle, so the
            # resulting "monotonicity" measured the bucketing, not the signal.
            ranked_c = sorted(comp, key=lambda t: comp[t])
            for i, t in enumerate(ranked_c):
                deciles[min(9, i * 10 // len(ranked_c))].append(excess[t])

            ordered = sorted(comp, key=lambda t: comp[t], reverse=True)
            cut = max(5, int(len(ordered) * QUINTILE))
            top, bottom = ordered[:cut], ordered[-cut:]

            arms = {
                "control_equal": equal(ordered),
                "loom_long": equal(top),
                "loom_long_short": long_short(top, bottom),
                "loom_sized": sized(comp),
                "control_random": equal(random.Random(f"r{snap['as_of']}").sample(ordered, cut)),
            }
            flat = {t: v for t, v in (snap.get("composite_flat") or {}).items() if t in fwd}
            if len(flat) >= MIN_UNIVERSE:
                of = sorted(flat, key=lambda t: flat[t], reverse=True)
                cf = max(5, int(len(of) * QUINTILE))
                arms["loom_long_flat"] = equal(of[:cf])
            for k in keys:
                if k == "composite":
                    continue
                have = {t: pcts[t][k] for t in comp if k in pcts[t]}
                if len(have) < MIN_UNIVERSE:
                    continue
                o = sorted(have, key=lambda t: have[t], reverse=True)
                c = max(5, int(len(o) * QUINTILE))
                arms[f"factor::{k}"] = long_short(o[:c], o[-c:])

            for name, w in arms.items():
                m = measure(w, tape, win, bench)
                if m:
                    m.pop("daily", None)
                    m["as_of"] = snap["as_of"]
                    books[name].append(m)

            # Where the difference between Loom and the control comes from,
            # name by name. Loom's book is a subset of the control's, so every
            # disagreement is either a name dropped or a name kept.
            held = set(top)
            w_ctrl = 1.0 / len(ordered)
            w_loom = 1.0 / len(top) if top else 0.0
            for t in ordered:
                # The contribution is the change in exposure times the outcome,
                # not the outcome alone. Unweighted, a name dropped from a
                # 740-name control counted as much as a name held at a twentieth
                # of the book, and the four effects summed to nothing
                # recognisable. Weighted, they sum to the actual difference
                # between the two books.
                delta = (w_loom if t in held else 0.0) - w_ctrl
                c = delta * excess[t]
                if t in held:
                    effects["captured_upside" if excess[t] > 0 else "added_loss"].append(c)
                else:
                    effects["missed_upside" if excess[t] > 0 else "avoided_loss"].append(c)

            regimes.append({"as_of": snap["as_of"], "bench": bench,
                            "scored": snap["n"], "with_forward": len(fwd),
                            "with_composite": len(comp),
                            "bench_vol": stats.annualised(
                                tape.path(BENCHMARK, win) or [], SESSIONS_PER_YEAR)})

            # Universe robustness: the same IC inside size strata and sectors.
            for label, lo, hi in (("mega", 0, 150), ("mid", 150, 450), ("small", 450, 10**9)):
                sel = [t for t in comp if (tape.rank.get(t) or 10**9) >= lo
                       and (tape.rank.get(t) or 10**9) < hi]
                if len(sel) >= 40:
                    rho = stats.spearman([comp[t] for t in sel], [excess[t] for t in sel])
                    if rho is not None:
                        strata_ic[label].append(rho)
            by_sector: dict[str, list[str]] = defaultdict(list)
            for t in comp:
                by_sector[tape.sectors.get(t, "unclassified")].append(t)
            for sec, sel in by_sector.items():
                if len(sel) >= 30:
                    rho = stats.spearman([comp[t] for t in sel], [excess[t] for t in sel])
                    if rho is not None:
                        sector_ic[sec].append(rho)

        results["horizons"][horizon] = {
            "ic": {k: _ic_summary(v, horizon) for k, v in ic_by_factor.items()},
            "fama_macbeth": _ic_summary(fm_slope, horizon),
            "books": {k: _book_summary(v, horizon) for k, v in books.items()},
            # The per-date rows are kept, not only their summary, because
            # conditioning on regime means re-grouping them afterwards and a
            # collapsed mean cannot be un-collapsed.
            "books_raw": {k: v for k, v in books.items()},
            "deciles": {str(d): {"n": len(v), "mean_excess": stats.mean(v)}
                        for d, v in sorted(deciles.items())},
            "effects": {k: {"n": len(v), "total": sum(v), "mean": stats.mean(v)}
                        for k, v in effects.items()},
            "strata_ic": {k: _ic_summary(v, horizon) for k, v in strata_ic.items()},
            "sector_ic": {k: _ic_summary(v, horizon) for k, v in sector_ic.items()},
            "regimes": regimes,
        }
    return results


def _ols_slope(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def _overlap_lags(horizon: int) -> int:
    """Monthly observations of an N-session holding period overlap for about
    N/21 months, and that is the autocorrelation the standard error must absorb."""
    return max(1, round(horizon / 21))


def _ic_summary(series: list[float], horizon: int) -> dict:
    t, p = stats.newey_west_t(series, _overlap_lags(horizon))
    point, lo, hi = stats.block_bootstrap_ci(series, resamples=2000, block=3)
    s = stats.stdev(series)
    return {"n": len(series), "mean": stats.mean(series), "stdev": s,
            "ir": (stats.mean(series) / s) if s else None,
            "t": t, "p": p, "ci_low": lo, "ci_high": hi,
            "share_positive": (sum(1 for v in series if v > 0) / len(series)) if series else None}


def _book_summary(rows: list[dict], horizon: int) -> dict:
    if not rows:
        return {"n": 0}
    ex = [r["excess"] for r in rows]
    t, p = stats.newey_west_t(ex, _overlap_lags(horizon))
    point, lo, hi = stats.block_bootstrap_ci(ex, resamples=2000, block=3)
    def avg(k): return stats.mean([r[k] for r in rows if r[k] is not None])
    return {"n": len(rows), "mean_excess": stats.mean(ex), "t": t, "p": p,
            "ci_low": lo, "ci_high": hi,
            "mean_return": avg("return"), "volatility": avg("volatility"),
            "max_drawdown": avg("max_drawdown"), "sharpe": avg("sharpe"),
            "sortino": avg("sortino"), "downside_deviation": avg("downside_deviation"),
            "hhi": avg("hhi"), "sector_hhi": avg("sector_hhi"),
            "max_weight": avg("max_weight"), "hit_rate": avg("hit_rate"),
            "n_names": avg("n_names"), "cash": avg("cash")}
