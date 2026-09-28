"""Rebuild Loom's factor layer point-in-time at every month end, and cache it.

This is the slow half of the benchmark and it contains no analysis, so a bug in
the measurement does not cost another rebuild. One JSON per decision date, each
holding every company's factor percentiles as Loom would have ranked them on
that day and nothing that was not knowable then.

Runs without a language model. That is the point: Loom's quantitative layer
exists back to 2020 and can be tested over regimes the document layer cannot
reach, on a free tier that allows twenty model calls a day.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import resource
import sys
import time
from datetime import date

from sqlalchemy import text as sqltext

from app.db.session import SessionLocal
from app.engine.quant.composite import build_composite
from app.engine.quant.relevance import weighted_composite
from app.engine.quant import runner as _runner
from app.engine.quant.runner import score_universe

HERE = pathlib.Path(__file__).parent
OUT = HERE / "scores"


PANEL_SIZE = 400


def panel(db) -> list[str]:
    """The fixed set of companies every cross-section is drawn from.

    Capped at four hundred, and the cap is a property of the hardware rather
    than a judgement about the right universe: a thousand companies' bars and
    facts held as ORM objects exceeded the free memory on an 8GB host and
    surfaced as `could not read block 0 ... I/O error`, which reads like
    corruption and was not.

    Stated in the report, because a percentile means "against comparable
    companies" and four hundred is a different comparison from nine hundred.
    """
    rows = db.execute(sqltext("""
        select c.ticker from companies c
        where c.sec_rank is not null
          and exists (select 1 from price_bars p where p.company_id = c.id)
          and exists (select 1 from structured_facts f where f.company_id = c.id)
        order by c.sec_rank
        limit :n
    """), {"n": PANEL_SIZE}).all()
    return [r.ticker for r in rows]


def _memoise_loaders() -> None:
    """Load the price and fact tables once instead of once per decision date.

    score_universe rebuilds both from scratch on every call, which is right for
    production, where it runs once a day. Here it runs a hundred times over a
    table that does not change between calls, and after the backfills that is
    2.9 million bars and 1.37 million facts re-read per date: measured at 127 to
    271 seconds each, against 50 before the backfills.

    Memoised here rather than in the engine, because the assumption that makes it
    safe — that nothing writes to either table for the duration — is a property
    of this script, not of the engine. The cached objects are only ever read:
    FactSeries.as_of returns a new instance rather than narrowing in place, and
    PriceHistory is immutable once constructed.
    """
    cache: dict[str, object] = {}
    real_prices = _runner._prices_by_company
    real_series = _runner._series_by_company

    def prices(db, companies):
        if "prices" not in cache:
            cache["prices"] = real_prices(db, companies)
            print(f"    cached prices for {len(cache['prices'])} companies", flush=True)
        return cache["prices"]

    def series(db, companies):
        if "series" not in cache:
            cache["series"] = real_series(db, companies)
            print(f"    cached fact series for {len(cache['series'])} companies", flush=True)
        return cache["series"]

    _runner._prices_by_company = prices
    _runner._series_by_company = series


def month_ends(first: date, last: date) -> list[date]:
    """Month ends from `first` to `last`. Calendar ends, not sessions: the
    scorer takes the last close on or before the date, so a weekend is handled
    by the price layer rather than by guessing a trading day here."""
    out, y, m = [], first.year, first.month
    while True:
        nm_y, nm_m = (y + 1, 1) if m == 12 else (y, m + 1)
        end = date(nm_y, nm_m, 1).toordinal() - 1
        d = date.fromordinal(end)
        if d > last:
            break
        if d >= first:
            out.append(d)
        y, m = nm_y, nm_m
    return out


def build(d: date, db, tickers: list[str]) -> dict:
    scored = score_universe(db, as_of=d, tickers=tickers)
    pct, flat, weighted = {}, {}, {}
    for t, ranks in scored.ranked.items():
        pct[t] = {k: round(r.percentile, 6) for k, r in ranks.items()
                  if r.percentile is not None}
        # Both foldings are stored, not one. `build_composite` averages every
        # surviving percentile; `weighted_composite` averages within themes
        # first and is what production actually shows. Keeping both is the only
        # way to ask whether the theme weighting earns its complexity.
        c = build_composite(ranks)
        if c is not None:
            flat[t] = c.score
        w = weighted_composite(pct[t], sector=scored.sector.get(t))
        if w is not None:
            weighted[t] = w.score
    return {
        "as_of": d.isoformat(),
        "n": len(scored.ranked),
        "percentiles": pct,
        "composite_flat": flat,
        "composite_weighted": weighted,
        "pool": {t: scored.group.get(t) for t in scored.ranked},
        "skipped_stale": len(scored.skipped_stale),
        "skipped_thin": len(scored.skipped_thin),
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0,
                    help="Build at most this many dates then exit, so a driver "
                         "loop can reclaim memory by restarting the process.")
    args = ap.parse_args()
    first = date(2018, 1, 31)
    last = date(2026, 8, 31)
    dates = month_ends(first, last)
    OUT.mkdir(exist_ok=True)
    db = SessionLocal()
    _memoise_loaders()
    names = panel(db)
    print(f"panel: {len(names)} companies", flush=True)
    todo = [d for d in dates if not (OUT / f"{d}.json").exists()]
    if args.limit:
        todo = todo[: args.limit]
    print(f"{len(dates)} month ends, {len(todo)} still to build", flush=True)

    for i, d in enumerate(todo, 1):
        started = time.time()
        try:
            payload = build(d, db, names)
        except Exception as exc:
            print(f"[{i}/{len(todo)}] {d} FAILED {type(exc).__name__}: {exc}", flush=True)
            db.rollback()
            continue
        (OUT / f"{d}.json").write_text(json.dumps(payload))
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 ** 3)
        print(f"[{i}/{len(todo)}] {d}  {payload['n']:4d} companies  "
              f"{time.time()-started:5.1f}s  peak RSS {rss:.2f}GB", flush=True)

    built = sorted(p.stem for p in OUT.glob("*.json"))
    print(f"=== done: {len(built)} cached score dates, {built[0]} .. {built[-1]} ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
