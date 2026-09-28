"""Run the mechanical benchmark and write the results out as JSON and tables."""
from __future__ import annotations

import json
import pathlib
import time

from app.db.session import SessionLocal

import quantstudy
import stats

HERE = pathlib.Path(__file__).parent


def main() -> int:
    started = time.time()
    db = SessionLocal()
    results = quantstudy.run(db)
    (HERE / "results_quant.json").write_text(json.dumps(results, indent=1, default=str))

    print(f"cross-sections used: {results['dates']}   "
          f"elapsed {time.time()-started:.0f}s\n")

    for horizon, block in results["horizons"].items():
        regimes = block["regimes"]
        if not regimes:
            print(f"=== {horizon} sessions: no usable cross-section ===\n")
            continue
        n = [r["with_composite"] for r in regimes]
        print(f"================ horizon {horizon} sessions "
              f"({len(regimes)} cross-sections, median {sorted(n)[len(n)//2]} names) ===")

        print("\n-- information coefficient (rank correlation of score with forward excess return)")
        fam = {k: v["p"] for k, v in block["ic"].items()}
        adj = stats.benjamini_hochberg(fam)
        rows = sorted(block["ic"].items(), key=lambda kv: -(abs(kv[1]["mean"] or 0)))
        print(f"   {'signal':30s} {'n':>3s} {'mean IC':>8s} {'IR':>6s} {'t':>7s} "
              f"{'p':>7s} {'p(BH)':>7s} {'>0':>5s}")
        for k, v in rows:
            p_adj, keep = adj.get(k, (None, False))
            f = lambda x, w=7, d=3: (f"{x:{w}.{d}f}" if x is not None else " " * (w - 1) + "-")
            print(f"   {k:30s} {v['n']:3d} {f(v['mean'],8,4)} {f(v['ir'],6,2)} "
                  f"{f(v['t'])} {f(v['p'])} {f(p_adj)} "
                  f"{f(v['share_positive'],5,2)}{'  *' if keep else ''}")

        print("\n-- Fama-MacBeth slope of forward excess return on the composite percentile")
        fm = block["fama_macbeth"]
        print(f"   n={fm['n']}  mean={fm['mean']}  t={fm['t']}  p={fm['p']}  "
              f"95% CI [{fm['ci_low']}, {fm['ci_high']}]")

        print("\n-- portfolios (mean excess over QQQ per holding period)")
        print(f"   {'book':26s} {'n':>3s} {'excess':>8s} {'t':>7s} {'p':>7s} {'vol':>7s} "
              f"{'maxDD':>7s} {'Sharpe':>7s} {'hit':>6s} {'HHI':>6s} {'names':>6s}")
        for k, v in sorted(block["books"].items()):
            if not v.get("n") or k.startswith("factor::"):
                continue
            f = lambda x, w=7, d=3: (f"{x:{w}.{d}f}" if x is not None else " " * (w - 1) + "-")
            print(f"   {k:26s} {v['n']:3d} {f(v['mean_excess'],8,4)} {f(v['t'])} {f(v['p'])} "
                  f"{f(v['volatility'])} {f(v['max_drawdown'])} {f(v['sharpe'])} "
                  f"{f(v['hit_rate'],6,2)} {f(v['hhi'],6,3)} {f(v['n_names'],6,1)}")

        print("\n-- calibration: mean forward excess return by composite decile")
        for d, v in sorted(block["deciles"].items(), key=lambda kv: int(kv[0])):
            bar = "#" * max(0, int(abs(v["mean_excess"] or 0) * 400))
            print(f"   decile {d:>2s}  n={v['n']:6d}  {100*(v['mean_excess'] or 0):+7.3f}%  {bar}")
        ds = sorted(block["deciles"].items(), key=lambda kv: int(kv[0]))
        mono = stats.spearman([int(k) for k, _ in ds],
                             [v["mean_excess"] or 0 for _, v in ds])
        print(f"   monotonicity (Spearman of decile against mean return): {mono}")

        print("\n-- where Loom's book differs from holding everything")
        for k, v in block["effects"].items():
            print(f"   {k:18s} n={v['n']:6d}  total {100*v['total']:+9.1f} pts  "
                  f"mean {100*(v['mean'] or 0):+6.2f}%")

        print("\n-- universe robustness (IC inside size strata)")
        for k, v in sorted(block["strata_ic"].items()):
            print(f"   {k:10s} n={v['n']:3d} mean IC {v['mean']}  t={v['t']}  p={v['p']}")

        print("\n-- sector robustness (IC inside sector)")
        for k, v in sorted(block["sector_ic"].items(), key=lambda kv: -(kv[1]["n"])):
            print(f"   {k:26s} n={v['n']:3d} mean IC {v['mean']}  t={v['t']}")

        print("\n-- regime conditioning: the same books, split by what the market did")
        ranked = sorted(regimes, key=lambda r: r["bench"])
        third = max(1, len(ranked) // 3)
        groups = {"falling": ranked[:third], "flat": ranked[third:-third],
                  "rising": ranked[-third:]}
        for label, group in groups.items():
            if not group:
                continue
            b = [g["bench"] for g in group]
            print(f"   {label} — {len(group)} cross-sections, benchmark "
                  f"{100*min(b):+.1f}% to {100*max(b):+.1f}%")
            dates = {g["as_of"] for g in group}
            print(f"      {'book':24s} {'n':>3s} {'excess':>8s} {'t':>7s} "
                  f"{'vol':>7s} {'maxDD':>7s} {'Sharpe':>7s}")
            for k, rows in sorted(block["books_raw"].items()):
                if k.startswith("factor::"):
                    continue
                sel = [r for r in rows if r["as_of"] in dates]
                if len(sel) < 3:
                    continue
                ex = [r["excess"] for r in sel]
                tt, _ = stats.newey_west_t(ex, max(1, round(horizon / 21)))
                f = lambda x, w=7, d=3: (f"{x:{w}.{d}f}" if x is not None else " " * (w - 1) + "-")
                av = lambda key: stats.mean([r[key] for r in sel if r[key] is not None])
                print(f"      {k:24s} {len(sel):3d} {f(stats.mean(ex),8,4)} {f(tt)} "
                      f"{f(av('volatility'))} {f(av('max_drawdown'))} {f(av('sharpe'))}")
            print()

        print("-- individual factors, long-short quintile books")
        print(f"   {'factor':30s} {'n':>3s} {'excess':>8s} {'t':>7s} {'Sharpe':>7s}")
        for k, v in sorted(block["books"].items()):
            if not k.startswith("factor::") or not v.get("n"):
                continue
            f = lambda x, w=7, d=3: (f"{x:{w}.{d}f}" if x is not None else " " * (w - 1) + "-")
            print(f"   {k[8:]:30s} {v['n']:3d} {f(v['mean_excess'],8,4)} {f(v['t'])} {f(v['sharpe'])}")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
