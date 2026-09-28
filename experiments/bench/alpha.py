"""Beta-adjusted alpha: the one test that separates defensiveness from skill.

Loom's top-quintile book beat an equal-weight control in every falling market
and lost in every rising one, across three horizons. That pattern is what a
lower-beta portfolio does by construction, so on its own it says nothing about
whether the ranking is informative. What settles it is whether any return
survives once the lower market exposure is accounted for.

Three standard errors are reported per row and the differences between them are
the point. Monthly observations of a multi-month holding period overlap, so the
same quarter appears in three rows; plain OLS treats them as independent and
overstates significance badly. The naive t on the 126-session book is 4.97, the
autocorrelation-robust t is 2.84, and on non-overlapping data it is 1.70.
"""
from __future__ import annotations

import json
import pathlib
from typing import Optional

import stats

HERE = pathlib.Path(__file__).parent
STEP = {"21": 1, "63": 3, "126": 6}      # months between non-overlapping draws
ARMS = ("loom_long", "loom_long_flat", "loom_long_short", "loom_sized")


def _beta(xs: list[float], ys: list[float]) -> Optional[float]:
    n = len(xs)
    if n < 3:
        return None
    mx, my = sum(xs) / n, sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den


def run() -> dict:
    q = json.loads((HERE / "results_quant.json").read_text())
    out: dict = {"rows": [], "note": __doc__.strip().splitlines()[0]}
    pvals: dict[str, Optional[float]] = {}

    for horizon, block in q["horizons"].items():
        raw = block.get("books_raw") or {}
        if "control_equal" not in raw:
            continue
        ctrl = {r["as_of"]: r["return"] for r in raw["control_equal"]}
        lags = max(1, round(int(horizon) / 21))
        for arm in ARMS:
            rows = sorted((r for r in raw.get(arm, []) if r["as_of"] in ctrl),
                          key=lambda r: r["as_of"])
            if len(rows) < 10:
                continue
            xs = [ctrl[r["as_of"]] for r in rows]
            ys = [r["return"] for r in rows]
            beta = _beta(xs, ys)
            if beta is None:
                continue
            adj = [y - beta * x for x, y in zip(xs, ys)]
            t_ols, p_ols = stats.t_test_mean(adj)
            t_nw, p_nw = stats.newey_west_t(adj, lags)
            sub = adj[:: STEP.get(horizon, 1)]
            t_sub, p_sub = stats.t_test_mean(sub)
            key = f"{arm}@{horizon}"
            pvals[key] = p_nw
            out["rows"].append({
                "key": key, "horizon": int(horizon), "arm": arm, "n": len(adj),
                "alpha": stats.mean(adj), "beta": beta,
                "t_ols": t_ols, "p_ols": p_ols,
                "t_nw": t_nw, "p_nw": p_nw,
                "n_nonoverlap": len(sub), "t_nonoverlap": t_sub, "p_nonoverlap": p_sub,
            })

    adjusted = stats.benjamini_hochberg(pvals)
    for row in out["rows"]:
        p_adj, keep = adjusted.get(row["key"], (None, False))
        row["p_bh"] = p_adj
        row["survives_bh"] = keep
    (HERE / "results_alpha.json").write_text(json.dumps(out, indent=1, default=str))
    return out


if __name__ == "__main__":
    r = run()
    print(f"{'horizon':>7s} {'arm':16s} {'n':>3s} {'alpha':>9s} {'beta':>6s} "
          f"{'t OLS':>6s} {'t NW':>6s} {'p NW':>7s} {'n_no':>5s} {'t_no':>6s} "
          f"{'p_no':>7s} {'p BH':>7s}")
    f = lambda x, d=2: (f"{x:.{d}f}" if x is not None else "     -")
    for row in sorted(r["rows"], key=lambda x: (x["horizon"], x["arm"])):
        print(f"{row['horizon']:7d} {row['arm']:16s} {row['n']:3d} "
              f"{100*row['alpha']:+8.3f}% {f(row['beta'],3):>6s} {f(row['t_ols']):>6s} "
              f"{f(row['t_nw']):>6s} {f(row['p_nw'],4):>7s} {row['n_nonoverlap']:5d} "
              f"{f(row['t_nonoverlap']):>6s} {f(row['p_nonoverlap'],4):>7s} "
              f"{f(row['p_bh'],4):>7s}{'  *' if row['survives_bh'] else ''}")
