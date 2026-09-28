"""Scoring whatever reader books exist, however few the quota allowed.

Written to be correct at n=8 as well as n=272, because on a twenty-call-a-day
tier the first useful analysis happens long before the grid is full. Every
contrast reports its own n and nothing is averaged across cells that are not
paired.
"""
from __future__ import annotations

import json
import pathlib
import re
from collections import defaultdict
from datetime import date
from typing import Optional

import quantstudy
import stats

HERE = pathlib.Path(__file__).parent
BOOKS = HERE / "books"
PACKETS = HERE / "packet_cache"


def load_books() -> list[dict]:
    out = []
    for f in sorted(BOOKS.glob("*.json")):
        try:
            d = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        if "positions" in d and "cell" in d:
            out.append(d)
    return out


def weights(book: dict) -> dict[str, float]:
    """Signed exposure per name. A pass is absent rather than zero-weighted, so
    concentration is measured over what was actually held."""
    w: dict[str, float] = {}
    for p in book["positions"]:
        a, wt = p.get("action"), float(p.get("weight") or 0)
        if wt <= 0 or a not in ("buy", "short"):
            continue
        w[p["ticker"].upper()] = wt if a == "buy" else -wt
    total = sum(abs(v) for v in w.values())
    # Agents overrun the gross limit. Scaling back is the honest correction: the
    # alternative is a book with leverage nobody authorised, whose volatility
    # would then be attributed to the treatment.
    if total > 1.0:
        w = {k: v / total for k, v in w.items()}
    return w


_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", (s or "").lower()).strip()


def grounding(book: dict) -> Optional[dict]:
    """How much of what the agent said it used was actually in front of it.

    A citation that does not appear in the packet is a confabulation, and an
    arm whose rationales are confabulated cannot be read as evidence that the
    research was used.
    """
    key = (f"t{book['tier']}__{book['decide']}__{book['arm']}"
           f"__u{book['seed'] if book['tier'] == 2 else 1}")
    f = PACKETS / f"{key}.json"
    if not f.exists():
        return None
    body = _norm(json.loads(f.read_text())["body"])
    cited = matched = 0
    with_citation = 0
    for p in book["positions"]:
        snippets = [s for s in (p.get("evidence_cited") or []) if s and s.strip()]
        if snippets:
            with_citation += 1
        for s in snippets:
            cited += 1
            if _norm(s)[:80] in body:
                matched += 1
    return {"citations": cited, "verbatim": matched,
            "grounding_rate": (matched / cited) if cited else None,
            "positions_citing": with_citation,
            "citation_rate": with_citation / max(1, len(book["positions"]))}


def calibration(rows: list[tuple[float, bool]]) -> Optional[dict]:
    """Reliability of stated conviction against what happened.

    Brier and expected calibration error together: Brier punishes being wrong,
    ECE punishes being wrong about how sure you were, and a reader can be good
    at one and useless at the other.
    """
    if len(rows) < 10:
        return None
    brier = sum((c - (1.0 if w else 0.0)) ** 2 for c, w in rows) / len(rows)
    bins: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for c, w in rows:
        bins[min(4, int(c * 5))].append((c, w))
    ece, curve = 0.0, []
    for b in sorted(bins):
        grp = bins[b]
        conf = sum(c for c, _ in grp) / len(grp)
        acc = sum(1 for _, w in grp if w) / len(grp)
        ece += len(grp) / len(rows) * abs(conf - acc)
        curve.append({"bin": b, "n": len(grp), "mean_conviction": round(conf, 3),
                      "actual_win_rate": round(acc, 3)})
    disc = stats.spearman([c for c, _ in rows], [1.0 if w else 0.0 for _, w in rows])
    return {"n": len(rows), "brier": round(brier, 4), "ece": round(ece, 4),
            "discrimination": disc, "curve": curve}


def run(db) -> dict:
    books = load_books()
    if not books:
        return {"books": 0}
    tape = quantstudy.Tape(db)

    scored: list[dict] = []
    calib_rows: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    ground: dict[str, list[dict]] = defaultdict(list)

    for b in books:
        d = date.fromisoformat(b["decide"])
        win = tape.window(d, b["sessions"])
        if win is None:
            continue
        bench = tape.total(quantstudy.BENCHMARK, win)
        if bench is None:
            continue
        w = weights(b)
        m = quantstudy.measure(w, tape, win, bench) if w else None
        if m is None:
            m = {"return": 0.0, "excess": -bench, "volatility": 0.0,
                 "max_drawdown": 0.0, "sharpe": None, "sortino": None,
                 "hhi": None, "max_weight": 0.0, "sector_hhi": None,
                 "n_names": 0, "gross": 0.0, "cash": 1.0, "hit_rate": None,
                 "downside_deviation": 0.0, "daily": []}
        m.pop("daily", None)

        # Sizing against selection, with no extra model call: the same names the
        # agent chose, held equally, isolates which names it picked; and the
        # agent's own weights on that same set, renormalised, isolate how much.
        names = [t for t, v in w.items() if v > 0]
        sel = quantstudy.measure(quantstudy.equal(names), tape, win, bench) if names else None

        g = grounding(b)
        if g:
            ground[b["arm"]].append(g)

        for p in b["positions"]:
            if p.get("action") != "buy":
                continue
            t = p["ticker"].upper()
            r = tape.total(t, win)
            if r is None:
                continue
            c = float(p.get("conviction") or 0)
            if 0 <= c <= 1:
                calib_rows[b["arm"]].append((c, r > bench))

        scored.append({**{k: v for k, v in m.items()},
                       "cell": b["cell"], "arm": b["arm"], "persona": b["persona"],
                       "seed": b["seed"], "decide": b["decide"],
                       "sessions": b["sessions"], "model": b.get("model"),
                       "selection_only_excess": sel["excess"] if sel else None,
                       "latency": b.get("latency_seconds"),
                       "input_tokens": b.get("input_tokens"),
                       "output_tokens": b.get("output_tokens"),
                       "n_offered": b.get("n_offered")})

    # Paired contrasts, only inside cells that share persona, seed and date.
    by_key = {(s["persona"], s["seed"], s["decide"], s["sessions"], s["arm"]): s
              for s in scored}
    pairs: dict[str, list[dict]] = defaultdict(list)
    for (pe, se, de, ss, arm), s in by_key.items():
        if arm == "control":
            continue
        ctrl = by_key.get((pe, se, de, ss, "control"))
        if ctrl is None:
            continue
        pairs[arm].append({
            "persona": pe, "seed": se, "decide": de,
            "excess": s["excess"] - ctrl["excess"],
            "volatility": (s["volatility"] or 0) - (ctrl["volatility"] or 0),
            "max_drawdown": s["max_drawdown"] - ctrl["max_drawdown"],
            "cash": s["cash"] - ctrl["cash"],
            "hhi": (s["hhi"] or 0) - (ctrl["hhi"] or 0),
            "n_names": s["n_names"] - ctrl["n_names"],
        })

    def summarise(rows: list[dict], field: str) -> dict:
        xs = [r[field] for r in rows if r[field] is not None]
        t, p = stats.t_test_mean(xs)
        point, lo, hi = stats.block_bootstrap_ci(xs, resamples=4000, block=2)
        return {"n": len(xs), "mean": stats.mean(xs), "t": t, "p": p,
                "ci_low": lo, "ci_high": hi,
                "positive": sum(1 for x in xs if x > 0)}

    # Consistency: the same packet read twice under different seeds.
    consistency: list[float] = []
    by_packet: dict[tuple, list[dict]] = defaultdict(list)
    for s in scored:
        by_packet[(s["arm"], s["persona"], s["decide"], s["sessions"])].append(s)
    for group in by_packet.values():
        if len(group) < 2:
            continue
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                consistency.append(abs(group[i]["excess"] - group[j]["excess"]))

    return {
        "books": len(scored),
        "per_book": scored,
        "arms": {arm: {f: summarise(rows, f) for f in
                       ("excess", "volatility", "max_drawdown", "cash", "hhi", "n_names")}
                 for arm, rows in pairs.items()},
        "calibration": {arm: calibration(rows) for arm, rows in calib_rows.items()},
        "grounding": {arm: {
            "books": len(v),
            "grounding_rate": stats.mean([x["grounding_rate"] for x in v
                                          if x["grounding_rate"] is not None]),
            "citation_rate": stats.mean([x["citation_rate"] for x in v]),
            "citations": sum(x["citations"] for x in v),
        } for arm, v in ground.items()},
        "consistency_mean_abs_gap": stats.mean(consistency),
        "efficiency": {arm: {
            "latency": stats.mean([s["latency"] for s in scored
                                   if s["arm"] == arm and s["latency"]]),
            "input_tokens": stats.mean([s["input_tokens"] for s in scored
                                        if s["arm"] == arm and s["input_tokens"]]),
            "output_tokens": stats.mean([s["output_tokens"] for s in scored
                                         if s["arm"] == arm and s["output_tokens"]]),
        } for arm in {s["arm"] for s in scored}},
    }
