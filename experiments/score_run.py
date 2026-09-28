"""Read the agent books, compute every metric, and report the contrasts."""
import json, pathlib, statistics as st
from datetime import date
from app.db.session import SessionLocal
import harness

BOOKS = pathlib.Path(__file__).parent / "books"
PERIODS = {"2026-03-25_2026-06-25": harness.Period(date(2026,3,25), date(2026,6,25)),
           "2026-06-25_2026-09-25": harness.Period(date(2026,6,25), date(2026,9,25))}
db = SessionLocal()

books, all_total, all_daily, all_window = [], {}, {}, {}
for plabel, p in PERIODS.items():
    u = (pathlib.Path(__file__).parent / "packets" / f"{plabel}_universe.txt").read_text().split()
    d, t, w = harness.load_returns(db, u, p)
    all_daily[plabel], all_total[plabel], all_window[plabel] = d, t, w

for f in sorted(BOOKS.glob("*.json")):
    # filename: {period}__{persona}__{arm}__seed{n}.json
    plabel, persona, arm, seed = f.stem.split("__")
    raw = json.loads(f.read_text())
    pos = {x["ticker"]: (x["action"], float(x["weight"]), float(x.get("conviction") or 0))
           for x in raw}
    b = harness.Book(arm=arm, persona=persona, seed=int(seed.replace("seed","")),
                     period=plabel, positions=pos)
    harness.measure(b, all_daily[plabel], all_window[plabel])
    books.append(b)

if not books:
    print("no books yet"); raise SystemExit

print(f"{'period':24s} {'persona':13s} {'arm':9s} {'sd':>2s} {'ret':>8s} {'vol':>7s} "
      f"{'maxDD':>7s} {'Sharpe':>7s} {'Sortino':>8s} {'cash':>6s} {'HHI':>5s} {'hit':>5s}")
for b in sorted(books, key=lambda x:(x.period,x.persona,x.arm,x.seed)):
    sh = f"{b.sharpe:7.2f}" if b.sharpe is not None else "      -"
    so = f"{b.sortino:8.2f}" if b.sortino is not None else "       -"
    hr = f"{100*b.hit_rate:4.0f}%" if b.hit_rate is not None else "    -"
    print(f"{b.period:24s} {b.persona:13s} {b.arm:9s} {b.seed:2d} {100*b.total_return:+7.2f}% "
          f"{100*b.volatility:6.1f}% {100*b.max_drawdown:6.1f}% {sh} {so} "
          f"{100*b.cash:5.0f}% {b.hhi:5.2f} {hr}")

print("\n=== arm averages (across personas, seeds and periods) ===")
print(f"{'arm':10s} {'n':>3s} {'ret':>8s} {'vol':>7s} {'maxDD':>7s} {'Sharpe':>7s} {'cash':>6s} {'shorts':>7s}")
for arm in harness.ARMS:
    g = [b for b in books if b.arm == arm]
    if not g: continue
    sh = [b.sharpe for b in g if b.sharpe is not None]
    print(f"{arm:10s} {len(g):3d} {100*st.mean(b.total_return for b in g):+7.2f}% "
          f"{100*st.mean(b.volatility for b in g):6.1f}% "
          f"{100*st.mean(b.max_drawdown for b in g):6.1f}% "
          f"{(st.mean(sh) if sh else 0):7.2f} "
          f"{100*st.mean(b.cash for b in g):5.0f}% "
          f"{st.mean(b.n_shorts for b in g):7.1f}")

print("\n=== paired treatment effect, per book (arm minus its own control) ===")
ctrl = {(b.persona,b.seed,b.period): b for b in books if b.arm=="control"}
for arm in ("evidence","full"):
    deltas = [(b.total_return - ctrl[(b.persona,b.seed,b.period)].total_return,
               b.volatility - ctrl[(b.persona,b.seed,b.period)].volatility,
               b.max_drawdown - ctrl[(b.persona,b.seed,b.period)].max_drawdown)
              for b in books if b.arm==arm and (b.persona,b.seed,b.period) in ctrl]
    if not deltas: continue
    r = [d[0] for d in deltas]
    print(f"  {arm:9s} n={len(r):2d}  return {100*st.mean(r):+6.2f} pts "
          f"(median {100*st.median(r):+6.2f}, {sum(1 for x in r if x>0)} of {len(r)} positive)"
          f"   vol {100*st.mean(d[1] for d in deltas):+5.2f}   "
          f"maxDD {100*st.mean(d[2] for d in deltas):+5.2f}")

print("\n=== where the effect comes from, per decision ===")
eff = []
for plabel in PERIODS:
    eff += harness.paired_effects([b for b in books if b.period==plabel], all_total[plabel])
for arm in ("evidence","full"):
    g = [e for e in eff if e.arm==arm]
    if not g: continue
    kinds = {}
    for e in g: kinds.setdefault(e.kind, []).append(e.contribution)
    print(f"  {arm}:")
    for k in ("missed_upside","avoided_loss","captured_upside","added_loss","unchanged"):
        if k not in kinds: continue
        v = kinds[k]
        print(f"     {k:16s} n={len(v):4d}  total {100*sum(v):+7.2f} pts")
    net = sum(sum(v) for k,v in kinds.items())
    print(f"     {'NET':16s}        {100*net:+7.2f} pts")
