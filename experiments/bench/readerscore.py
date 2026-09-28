"""Scoring the reader benchmark: what Loom did to the decision, not to the price.

Every comparison is paired inside a (date, persona, seed) cell, so the only thing
differing between the two books being compared is what the reader was handed.
Unpaired books are never averaged together.

Two separations are deliberate and load-bearing. The economic category of a
change is decided by the realised outcome, never by what the reader said about
it — an eloquent thesis is not evidence of a good decision. And the reasoning
evaluation never sees a return, while the outcome scoring never sees the
reasoning, so neither can contaminate the other.
"""
from __future__ import annotations

import json
import math
import pathlib
import random
import re
from collections import defaultdict
from datetime import date
from typing import Optional

import quantstudy
import readerbench as rb
import spec
import stats

HERE = pathlib.Path(__file__).parent
DECISIONS = HERE / "decisions"
TICKERMETA = HERE / "tickermeta"
PACKETS = HERE / "packet_cache"

_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", (s or "").lower()).strip()


def _sign(x: Optional[float]) -> int:
    if x is None or abs(x) < 1e-12:
        return 0
    return 1 if x > 0 else -1


# ------------------------------------------------------------------ loading


def load() -> list[dict]:
    out = []
    for f in sorted(DECISIONS.glob("*.json")):
        d = json.loads(f.read_text())
        if d.get("decision") and d["decision"]["positions"]:
            out.append(d)
    return out


def tickermeta(as_of: str) -> dict:
    f = TICKERMETA / f"{as_of}.json"
    return json.loads(f.read_text()) if f.exists() else {}


def packet_body(as_of: str, arm: str) -> str:
    f = PACKETS / f"t1__{as_of}__{arm}__u1.json"
    return json.loads(f.read_text())["body"] if f.exists() else ""


# ------------------------------------------------------------------ outcomes


def score_books(records: list[dict], tape: quantstudy.Tape) -> list[dict]:
    """Portfolio outcome for each decision, plus the per-name returns behind it."""
    scored = []
    for rec in records:
        as_of = date.fromisoformat(rec["as_of"])
        win = tape.window(as_of, rec["sessions"])
        if win is None:
            continue
        bench = tape.total(quantstudy.BENCHMARK, win)
        if bench is None:
            continue
        w = rb.weights(rec["decision"])
        m = quantstudy.measure(w, tape, win, bench)
        if m is None:
            continue
        m.pop("daily", None)
        per_name = {}
        for t in w:
            r = tape.total(t, win)
            if r is not None:
                per_name[t] = r - bench
        scored.append({
            **rec, "weights": w, "benchmark": bench, "outcome": m,
            "excess_by_name": per_name,
            "turnover": sum(abs(v) for v in w.values()),
        })
    return scored


# ------------------------------------------------------- paired decomposition

CATEGORIES = ("captured_upside", "missed_upside", "avoided_loss", "added_loss")


def decompose(treatment: dict, control: dict) -> dict:
    """What the treatment changed, and what each change was worth.

    Contribution is the change in signed exposure times the realised
    benchmark-adjusted return, so the four categories sum to the difference
    between the two books rather than to a number with no referent. The category
    is chosen by the outcome, not by the reader's narrative.
    """
    names = set(treatment["weights"]) | set(control["weights"])
    excess = {**control["excess_by_name"], **treatment["excess_by_name"]}
    buckets: dict[str, list[float]] = {c: [] for c in CATEGORIES}
    changes = {"added": [], "removed": [], "resized": [], "flipped": []}

    for t in names:
        wt = treatment["weights"].get(t, 0.0)
        wc = control["weights"].get(t, 0.0)
        delta = wt - wc
        if abs(delta) < 1e-9:
            continue
        e = excess.get(t)
        if e is None:
            continue
        contribution = delta * e
        # More exposure to a winner is captured upside; more to a loser is added
        # loss; less exposure to a winner is missed upside; less to a loser is
        # avoided loss. Signed exposure means a short of a faller lands in the
        # right bucket without a special case.
        if delta > 0:
            buckets["captured_upside" if e > 0 else "added_loss"].append(contribution)
        else:
            buckets["missed_upside" if e > 0 else "avoided_loss"].append(contribution)

        if wc == 0:
            changes["added"].append(t)
        elif wt == 0:
            changes["removed"].append(t)
        elif _sign(wt) != _sign(wc):
            changes["flipped"].append(t)
        else:
            changes["resized"].append(t)

    return {
        "effects": {c: {"n": len(v), "total": sum(v)} for c, v in buckets.items()},
        "net": sum(sum(v) for v in buckets.values()),
        "positions_added": len(changes["added"]),
        "positions_removed": len(changes["removed"]),
        "positions_resized": len(changes["resized"]),
        "positions_flipped": len(changes["flipped"]),
        "cash_change": treatment["outcome"]["cash"] - control["outcome"]["cash"],
        "hhi_change": (treatment["outcome"]["hhi"] or 0) - (control["outcome"]["hhi"] or 0),
        "gross_change": treatment["outcome"]["gross"] - control["outcome"]["gross"],
        "names_change": treatment["outcome"]["n_names"] - control["outcome"]["n_names"],
        "conviction_change": _mean_conf(treatment) - _mean_conf(control)
        if _mean_conf(treatment) is not None and _mean_conf(control) is not None else None,
    }


def _mean_conf(rec: dict) -> Optional[float]:
    xs = [p["confidence"] for p in rec["decision"]["positions"]
          if p["confidence"] is not None]
    return stats.mean(xs)


# --------------------------------------------------------- evidence use


def evidence_use(rec: dict) -> dict:
    """Did the reader use the research, or only repeat its conclusion.

    A citation counts only if it appears verbatim in that reader's own packet.
    The verdict line is checked separately: a reader who cites nothing but the
    verdict has followed an answer rather than read an argument, and that is the
    distinction the whole benchmark turns on.
    """
    body = _norm(packet_body(rec["as_of"], rec["arm"]))
    verdict_lines = [_norm(l) for l in packet_body(rec["as_of"], rec["arm"]).splitlines()
                     if l.startswith("LOOM VERDICT")]
    cited = grounded = verdict_only = 0
    unique: set[str] = set()
    with_counter = 0
    for p in rec["decision"]["positions"]:
        if p["counterargument"].strip():
            with_counter += 1
        for raw in p["evidence"]:
            cited += 1
            snippet = _norm(raw)[:80]
            if snippet and snippet in body:
                grounded += 1
                unique.add(snippet)
            if any(snippet and snippet in v for v in verdict_lines):
                verdict_only += 1
    n_pos = len(rec["decision"]["positions"]) or 1
    return {
        "citations": cited,
        "grounded": grounded,
        "grounding_rate": (grounded / cited) if cited else None,
        "unique_findings": len(unique),
        "citations_per_position": cited / n_pos,
        "verdict_citations": verdict_only,
        "verdict_only_share": (verdict_only / cited) if cited else None,
        "counterargument_rate": with_counter / n_pos,
    }


# --------------------------------------------------------- anchoring


def anchoring(rec: dict) -> Optional[dict]:
    """Does the reader follow the verdict past what the evidence supports?

    Reframed from the obvious form, because the obvious form has no cases. The
    verdict is a monotone function of the same residual the evidence direction is
    computed from, so it never points the opposite way: measured across all five
    dates, verdict and evidence disagree on exactly zero companies.

    What does happen, 8 to 11 times per date, is that the verdict stays silent
    while the evidence leans, because the sufficiency gate refused. So anchoring
    is testable here only as *suppression*: on those names, does a reader given
    the verdict take a smaller position than a reader given only the evidence?
    """
    meta = tickermeta(rec["as_of"])
    if not meta:
        return None
    held = rec["weights"]
    agree_verdict = total_verdict = 0
    agree_evidence = total_evidence = 0
    suppressed: list[str] = []
    for t, m in meta.items():
        w = held.get(t, 0.0)
        if m["verdict_sign"]:
            total_verdict += 1
            if _sign(w) == m["verdict_sign"]:
                agree_verdict += 1
        if m["evidence_sign"]:
            total_evidence += 1
            if _sign(w) == m["evidence_sign"]:
                agree_evidence += 1
        if not m["verdict_sign"] and m["evidence_sign"]:
            suppressed.append(t)
    return {
        "agreement_with_verdict": (agree_verdict / total_verdict) if total_verdict else None,
        "agreement_with_evidence": (agree_evidence / total_evidence) if total_evidence else None,
        "n_verdict_stated": total_verdict,
        "n_evidence_leaning": total_evidence,
        "silent_verdict_names": suppressed,
        "exposure_on_silent_names": sum(abs(held.get(t, 0.0)) for t in suppressed),
        "count_held_on_silent_names": sum(1 for t in suppressed if t in held),
    }


# --------------------------------------------------------- statistics


def paired_effect(pairs: list[float], *, resamples: int = 10_000,
                  seed: int = 11) -> dict:
    """A paired treatment effect with a bootstrap interval and a permutation test.

    The permutation test is the honest one at this sample size: it assumes only
    that, under the null, the sign of each pair's difference is exchangeable.
    """
    if not pairs:
        return {"n": 0}
    point = stats.mean(pairs)
    t, p_t = stats.t_test_mean(pairs)
    _, lo, hi = stats.block_bootstrap_ci(pairs, resamples=resamples, block=2, seed=seed)
    rnd = random.Random(seed)
    extreme = 0
    for _ in range(resamples):
        flipped = stats.mean([x if rnd.random() < 0.5 else -x for x in pairs])
        if abs(flipped) >= abs(point) - 1e-15:
            extreme += 1
    return {
        "n": len(pairs), "estimate": point, "ci_low": lo, "ci_high": hi,
        "t": t, "p_t": p_t, "p_permutation": extreme / resamples,
        "n_positive": sum(1 for x in pairs if x > 0),
        "median": stats.median(pairs) if hasattr(stats, "median") else None,
    }
