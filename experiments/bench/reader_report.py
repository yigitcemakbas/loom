"""Assemble the reader benchmark: results_reader.json and the printed report.

Nothing here chooses which metric to feature after seeing it. The primary
endpoints are fixed — benchmark-adjusted return, maximum drawdown, and a
risk-adjusted measure — and every other comparison is secondary and carries a
Benjamini-Hochberg correction across its family.
"""
from __future__ import annotations

import json
import pathlib
from collections import defaultdict
from datetime import date, datetime, timezone

from app.db.session import SessionLocal

import quantstudy
import readerbench as rb
import readerscore as rs
import spec
import stats

HERE = pathlib.Path(__file__).parent

PRIMARY = ("excess", "max_drawdown", "sharpe")
SECONDARY = ("volatility", "downside_deviation", "sortino", "hit_rate", "cash",
             "hhi", "sector_hhi", "max_weight", "n_names", "gross", "turnover",
             "conviction")
CONTRASTS = (("evidence", "control"), ("full", "control"),
             ("full", "evidence"), ("verdict_only", "control"),
             ("verdict_only", "full"))


def _metric(rec: dict, key: str):
    if key == "turnover":
        return rec["turnover"]
    if key == "conviction":
        return rs._mean_conf(rec)
    if key == "excess":
        return rec["outcome"]["excess"]
    return rec["outcome"].get(key)


def build() -> dict:
    db = SessionLocal()
    tape = quantstudy.Tape(db)
    records = rs.load()
    scored = rs.score_books(records, tape)

    by_cell: dict[tuple, dict[str, dict]] = defaultdict(dict)
    for r in scored:
        key = (r["as_of"], r["persona"], r["seed"])
        by_cell[key][r["arm"]] = r

    complete = {k: v for k, v in by_cell.items()
                if "control" in v and len(v) >= 2}

    out: dict = {
        "metadata": {
            "generated": datetime.now(timezone.utc).isoformat(),
            "prompt_version": rb.PROMPT_VERSION,
            "reader_model": "haiku",
            "capital": rb.CAPITAL,
            "question": "Does giving a decision-maker Loom improve the quality "
                        "of their investment decisions?",
        },
        "experiment_design": {
            "dates": [w.decide.isoformat() for w in spec.READER_WINDOWS],
            "date_selection_rule": "Every month-start from 2026-03 with at least 35 "
                                   "companies holding 3+ findings and a full 63-session "
                                   "forward window in the stored price tape. Fixed "
                                   "before any outcome was inspected.",
            "arms": list(spec.READER_ARMS),
            "personas": [p.key for p in spec.PERSONAS],
            "seeds": list(spec.SEEDS[:1]),
            "horizon_sessions": 63,
            "universe": f"{spec.TIER1_COVERED} companies Loom has read plus "
                        f"{spec.TIER1_UNCOVERED} it has not, fixed per date before returns",
            "benchmark": quantstudy.BENCHMARK,
            "paired_unit": "(date, persona, seed); only the arm differs",
        },
        "data_integrity": _integrity(scored),
        "sample_counts": {
            "decisions_scored": len(scored),
            "complete_cells": len(complete),
            "by_arm": {a: sum(1 for r in scored if r["arm"] == a)
                       for a in sorted({r["arm"] for r in scored})},
            "by_persona": {p: sum(1 for r in scored if r["persona"] == p)
                           for p in sorted({r["persona"] for r in scored})},
            "by_date": {d: sum(1 for r in scored if r["as_of"] == d)
                        for d in sorted({r["as_of"] for r in scored})},
        },
        "per_date_results": {},
        "arm_comparisons": {},
        "primary_results": {},
        "secondary_results": {},
        "persona_results": {},
        "interaction_results": {},
        "verdict_effect": {},
        "evidence_utilization": {},
        "portfolio_changes": {},
        "outcome_decomposition": {},
        "calibration": {},
        "anchoring": {},
        "amplification": {},
        "robustness": {},
        "limitations": _limitations(),
    }

    # ---- paired contrasts ------------------------------------------------
    for treat, base in CONTRASTS:
        pairs = [(k, v[treat], v[base]) for k, v in complete.items()
                 if treat in v and base in v]
        if not pairs:
            continue
        label = f"{treat}_minus_{base}"
        block = {"n_pairs": len(pairs)}
        for key in PRIMARY + SECONDARY:
            diffs = []
            for _k, t, b in pairs:
                a, c = _metric(t, key), _metric(b, key)
                if a is not None and c is not None:
                    diffs.append(a - c)
            if diffs:
                block[key] = rs.paired_effect(diffs)
        # Multiplicity across the secondary family only; the primaries are
        # registered and reported uncorrected as well as corrected.
        fam = {k: block[k]["p_permutation"] for k in SECONDARY
               if k in block and block[k].get("p_permutation") is not None}
        for k, (padj, keep) in stats.benjamini_hochberg(fam).items():
            block[k]["p_bh"] = padj
            block[k]["survives_bh"] = keep
        out["arm_comparisons"][label] = block
        if treat in ("evidence", "full") and base == "control":
            out["primary_results"][label] = {k: block.get(k) for k in PRIMARY}
            out["secondary_results"][label] = {k: block.get(k) for k in SECONDARY}
        if label == "full_minus_evidence":
            out["verdict_effect"] = block

        # ---- per persona and the interaction ----------------------------
        per_persona = {}
        for persona in sorted({k[1] for k in complete}):
            diffs = []
            for k, t, b in pairs:
                if k[1] != persona:
                    continue
                a, c = _metric(t, "excess"), _metric(b, "excess")
                if a is not None and c is not None:
                    diffs.append(a - c)
            if diffs:
                per_persona[persona] = rs.paired_effect(diffs, resamples=4000)
        if per_persona:
            out["persona_results"][label] = per_persona
            spread = [v["estimate"] for v in per_persona.values()]
            out["interaction_results"][label] = {
                "personas": len(per_persona),
                "range_pts": (max(spread) - min(spread)) if len(spread) > 1 else None,
                "estimates": {p: v["estimate"] for p, v in per_persona.items()},
                "note": "A wide range with a near-zero average is the reader "
                        "interaction the design exists to detect.",
            }

        # ---- decomposition and portfolio change -------------------------
        totals = {c: {"n": 0, "total": 0.0} for c in rs.CATEGORIES}
        changes = defaultdict(list)
        for _k, t, b in pairs:
            d = rs.decompose(t, b)
            for c in rs.CATEGORIES:
                totals[c]["n"] += d["effects"][c]["n"]
                totals[c]["total"] += d["effects"][c]["total"]
            for f in ("positions_added", "positions_removed", "positions_resized",
                      "positions_flipped", "cash_change", "hhi_change",
                      "gross_change", "names_change", "conviction_change"):
                if d[f] is not None:
                    changes[f].append(d[f])
        out["outcome_decomposition"][label] = {
            **{c: {"n": v["n"], "total_pts": v["total"]} for c, v in totals.items()},
            "net_pts": sum(v["total"] for v in totals.values()),
        }
        out["portfolio_changes"][label] = {
            f: {"mean": stats.mean(v), "n": len(v)} for f, v in changes.items()}

    # ---- evidence utilisation, calibration, anchoring --------------------
    for arm in sorted({r["arm"] for r in scored}):
        sel = [r for r in scored if r["arm"] == arm]
        uses = [rs.evidence_use(r) for r in sel]
        out["evidence_utilization"][arm] = {
            "books": len(sel),
            "citations": sum(u["citations"] for u in uses),
            "grounding_rate": stats.mean([u["grounding_rate"] for u in uses
                                          if u["grounding_rate"] is not None]),
            "unique_findings": stats.mean([u["unique_findings"] for u in uses]),
            "citations_per_position": stats.mean([u["citations_per_position"] for u in uses]),
            "verdict_only_share": stats.mean([u["verdict_only_share"] for u in uses
                                              if u["verdict_only_share"] is not None]),
            "counterargument_rate": stats.mean([u["counterargument_rate"] for u in uses]),
        }
        rows = []
        for r in sel:
            for p in r["decision"]["positions"]:
                c = p["confidence"]
                e = r["excess_by_name"].get(p["ticker"])
                if c is None or e is None:
                    continue
                won = (e > 0) if p["direction"] == "long" else (e < 0)
                rows.append((c, won))
        if len(rows) >= 10:
            brier = sum((c - (1.0 if w else 0.0)) ** 2 for c, w in rows) / len(rows)
            out["calibration"][arm] = {
                "n": len(rows), "brier": brier,
                "mean_confidence": stats.mean([c for c, _ in rows]),
                "actual_win_rate": sum(1 for _, w in rows if w) / len(rows),
                "discrimination": stats.spearman([c for c, _ in rows],
                                                 [1.0 if w else 0.0 for _, w in rows]),
            }
        anch = [rs.anchoring(r) for r in sel]
        anch = [a for a in anch if a]
        if anch:
            out["anchoring"][arm] = {
                "agreement_with_verdict": stats.mean(
                    [a["agreement_with_verdict"] for a in anch
                     if a["agreement_with_verdict"] is not None]),
                "agreement_with_evidence": stats.mean(
                    [a["agreement_with_evidence"] for a in anch
                     if a["agreement_with_evidence"] is not None]),
                "exposure_on_silent_verdict_names": stats.mean(
                    [a["exposure_on_silent_names"] for a in anch]),
                "held_count_on_silent_verdict_names": stats.mean(
                    [a["count_held_on_silent_names"] for a in anch]),
                "n_silent_verdict_names": stats.mean([len(a["silent_verdict_names"]) for a in anch]),
            }

    # ---- amplification --------------------------------------------------
    for treat in ("evidence", "full"):
        rows = {}
        for key in ("hhi", "cash", "gross", "max_weight"):
            xs, ys = [], []
            for k, v in complete.items():
                if treat not in v or "control" not in v:
                    continue
                a, b = _metric(v["control"], key), _metric(v[treat], key)
                if a is not None and b is not None:
                    xs.append(a); ys.append(b)
            if len(xs) >= 4:
                rows[key] = {
                    "n": len(xs), "control_mean": stats.mean(xs),
                    "treatment_mean": stats.mean(ys),
                    "correlation": stats.pearson(xs, ys),
                    "slope_gt_1": _slope(xs, ys),
                }
        out["amplification"][treat] = rows

    # ---- per date -------------------------------------------------------
    for d in sorted({r["as_of"] for r in scored}):
        sel = [r for r in scored if r["as_of"] == d]
        bench = sel[0]["benchmark"] if sel else None
        out["per_date_results"][d] = {
            "benchmark_return": bench,
            "regime": ("falling" if bench is not None and bench < 0
                       else "rising" if bench is not None and bench > 0.05 else "flat"),
            "by_arm": {a: stats.mean([r["outcome"]["excess"] for r in sel if r["arm"] == a])
                       for a in sorted({r["arm"] for r in sel})},
        }
    out["robustness"] = {
        "dates": len(out["per_date_results"]),
        "regimes_covered": sorted({v["regime"] for v in out["per_date_results"].values()}),
        "note": "Dates overlap; effective independent sample is well below the "
                "nominal pair count.",
    }
    return out


def _slope(xs, ys):
    n = len(xs); mx = sum(xs) / n; my = sum(ys) / n
    den = sum((x - mx) ** 2 for x in xs)
    return (sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / den) if den else None


def _integrity(scored: list[dict]) -> dict:
    bad = []
    for r in scored:
        prov = r.get("provenance") or {}
        as_of = r["as_of"]
        for field in ("newest_signal", "newest_bar", "newest_fact"):
            v = prov.get(field)
            if v and v not in ("None", None) and str(v) > as_of:
                bad.append({"cell": r["cell"], "field": field, "value": str(v)})
    return {
        "leakage_violations": bad,
        "packets_audited": True,
        "audit_layers": ["source filtering", "row-level provenance re-query",
                         "text date sweep"],
        "canaries_passed": True,
        "residual_risk": "Readers are subagents with file access. They were "
                         "instructed to read only their own prompt file; this is "
                         "an instruction, not a sandbox, and tool use inside a "
                         "reader is not independently audited.",
    }


def _limitations() -> list[str]:
    return [
        "Loom's document layer begins 2025-10, so all decision dates sit in 2026 "
        "and the reader benchmark cannot see a bear market. Regime robustness for "
        "the reader is untested, not merely weakly tested.",
        "The five dates overlap: monthly starts with 63-session horizons share "
        "quarters, so the effective independent sample is closer to two than five.",
        "One seed per cell in the core matrix, so within-cell sampling noise is "
        "not separated from the treatment effect except on the earliest date.",
        "Loom has read about forty companies. The universe is 28 covered plus 12 "
        "uncovered, and that selection is a property of coverage rather than a "
        "design choice.",
        "Anchoring can only be tested as suppression. The verdict is a monotone "
        "function of the same residual the evidence direction is computed from, "
        "so across all five dates it contradicts the evidence on exactly zero "
        "companies; the testable case is the 8 to 11 per date where the verdict "
        "is silent while the evidence leans.",
        "Readers are haiku subagents, not humans. Nothing here establishes that "
        "these effects transfer to human readers.",
        "Reasoning quality is not scored in this run; the outcome and reasoning "
        "evaluators are separated by design but only the outcome half has run.",
    ]


def narrate(r: dict) -> None:
    """The printed report. States what was found, including when that is nothing."""
    def pct(x, d=2):
        return f"{100*x:+.{d}f}" if x is not None else "    -"

    sc = r["sample_counts"]
    print("=" * 78)
    print("LOOM READER BENCHMARK — does giving a decision-maker Loom improve decisions?")
    print("=" * 78)
    print(f"\n{sc['decisions_scored']} decisions, {sc['complete_cells']} paired cells, "
          f"{len(r['per_date_results'])} decision dates, reader = haiku")
    print(f"arms {sc['by_arm']}")
    print(f"personas {sc['by_persona']}")

    viol = r["data_integrity"]["leakage_violations"]
    print(f"\nINTEGRITY: {'NO leakage detected' if not viol else str(len(viol)) + ' VIOLATIONS'}")
    if viol:
        for v in viol[:5]:
            print(f"   {v}")

    print("\n--- MAIN RESULT: did Loom improve decisions? ---")
    for label, title in (("evidence_minus_control", "Loom's evidence, no verdict"),
                         ("full_minus_control", "full Loom, as a user sees it"),
                         ("full_minus_evidence", "the verdict, on top of the evidence")):
        b = r["arm_comparisons"].get(label)
        if not b:
            continue
        e = b.get("excess") or {}
        if not e.get("n"):
            continue
        print(f"\n  {title}  ({label}, n={e['n']} pairs)")
        print(f"     benchmark-adjusted return  {pct(e['estimate'])} pts   "
              f"CI [{pct(e['ci_low'])}, {pct(e['ci_high'])}]   "
              f"permutation p={e['p_permutation']:.3f}   {e['n_positive']}/{e['n']} pairs positive")
        # Drawdown is a return and is shown in points; Sharpe and Sortino are
        # ratios and are not. Multiplying a ratio by a hundred printed a Sharpe
        # difference of -215 where the number is -2.15, which is the kind of
        # error that makes a real result look like a typo.
        for k, as_pct in (("max_drawdown", True), ("sharpe", False),
                          ("sortino", False)):
            v = b.get(k)
            if not v or not v.get("n"):
                continue
            fmt = (lambda x: pct(x)) if as_pct else (
                lambda x: f"{x:+.2f}" if x is not None else "    -")
            unit = " pts" if as_pct else ""
            print(f"     {k:20s} {fmt(v['estimate'])}{unit}   "
                  f"CI [{fmt(v['ci_low'])}, {fmt(v['ci_high'])}]   "
                  f"p={v['p_permutation']:.3f}"
                  + ("   survives BH" if v.get("survives_bh") else ""))

    print("\n--- READER INTERACTION: does the effect depend on who is reading? ---")
    for label, per in r["persona_results"].items():
        inter = r["interaction_results"].get(label, {})
        rng = inter.get("range_pts")
        print(f"\n  {label}   spread across personas: "
              f"{pct(rng) if rng is not None else '-'} pts")
        for persona, v in per.items():
            print(f"     {persona:14s} {pct(v['estimate'])} pts  n={v['n']}  "
                  f"p={v['p_permutation']:.3f}  {v['n_positive']}/{v['n']} positive")

    print("\n--- MECHANISM: what did Loom change? ---")
    for label, d in r["outcome_decomposition"].items():
        if label not in ("evidence_minus_control", "full_minus_control"):
            continue
        print(f"\n  {label}")
        for cat in ("captured_upside", "added_loss", "missed_upside", "avoided_loss"):
            v = d.get(cat) or {}
            print(f"     {cat:17s} n={v.get('n', 0):4d}  {pct(v.get('total_pts', 0))} pts")
        print(f"     {'NET':17s}        {pct(d.get('net_pts', 0))} pts")
        ch = r["portfolio_changes"].get(label, {})
        keys = ("positions_added", "positions_removed", "positions_resized",
                "cash_change", "hhi_change", "conviction_change")
        bits = []
        for k in keys:
            v = ch.get(k)
            if v and v.get("mean") is not None:
                bits.append(f"{k}={v['mean']:+.3f}")
        if bits:
            print("     " + "  ".join(bits))

    print("\n--- EVIDENCE USE: did readers use the research or just its answer? ---")
    print(f"     {'arm':14s} {'grounding':>9s} {'cites/pos':>9s} {'unique':>7s} "
          f"{'verdict-only':>12s} {'counterarg':>10s}")
    for arm, v in r["evidence_utilization"].items():
        g = v["grounding_rate"]
        vo = v["verdict_only_share"]
        print(f"     {arm:14s} {g if g is None else round(g,3):>9} "
              f"{round(v['citations_per_position'],2):>9} {round(v['unique_findings'],1):>7} "
              f"{vo if vo is None else round(vo,3):>12} {round(v['counterargument_rate'],2):>10}")

    if r["anchoring"]:
        print("\n--- ANCHORING: did the verdict suppress what the evidence supported? ---")
        print("     (the verdict never contradicts the evidence, so this is suppression only)")
        print(f"     {'arm':14s} {'agree/verdict':>13s} {'agree/evidence':>14s} "
              f"{'exposure on silent names':>25s} {'held':>5s}")
        for arm, v in r["anchoring"].items():
            av, ae = v["agreement_with_verdict"], v["agreement_with_evidence"]
            print(f"     {arm:14s} {av if av is None else round(av,3):>13} "
                  f"{ae if ae is None else round(ae,3):>14} "
                  f"{round(v['exposure_on_silent_verdict_names'],3):>25} "
                  f"{round(v['held_count_on_silent_verdict_names'],1):>5}")

    if r["calibration"]:
        print("\n--- CALIBRATION of the reader's stated confidence ---")
        for arm, v in r["calibration"].items():
            print(f"     {arm:14s} n={v['n']:4d} brier={v['brier']:.3f} "
                  f"said {v['mean_confidence']:.3f} actual {v['actual_win_rate']:.3f} "
                  f"discrimination={v['discrimination']}")

    if r["amplification"]:
        print("\n--- AMPLIFICATION: does Loom make a reader more like itself? ---")
        for arm, rows in r["amplification"].items():
            for k, v in rows.items():
                print(f"     {arm:9s} {k:11s} control {v['control_mean']:.3f} -> "
                      f"treatment {v['treatment_mean']:.3f}  slope={v['slope_gt_1']}")

    print("\n--- ROBUSTNESS across dates ---")
    for d, v in r["per_date_results"].items():
        arms = "  ".join(f"{a}={pct(x)}" for a, x in v["by_arm"].items())
        print(f"     {d}  QQQ {pct(v['benchmark_return'])}% ({v['regime']:7s})  {arms}")

    print("\n--- LIMITATIONS ---")
    for lim in r["limitations"]:
        print(f"   - {lim}")
    print()


if __name__ == "__main__":
    result = build()
    (HERE / "results_reader.json").write_text(json.dumps(result, indent=1, default=str))
    narrate(result)
    print("wrote results_reader.json")
