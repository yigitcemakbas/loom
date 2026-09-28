"""Render the benchmark as a PDF: the design, the gates, then every number.

Deliberately not interpretive. The tables and the declared limitations go in the
document; what they mean is argued separately, where it can be challenged.
"""
from __future__ import annotations

import json
import pathlib
from datetime import date

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import (KeepTogether, PageBreak, Paragraph, SimpleDocTemplate,
                                Spacer, Table, TableStyle)

from app.db.session import SessionLocal

import alpha as alpha_mod
import reader_metrics
import stats

HERE = pathlib.Path(__file__).parent
S = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=S["Normal"], fontSize=9, leading=13)
SMALL = ParagraphStyle("small", parent=S["Normal"], fontSize=7.5, leading=10,
                       textColor=colors.HexColor("#444444"))
H1 = ParagraphStyle("h1", parent=S["Heading1"], fontSize=17, spaceAfter=6)
H2 = ParagraphStyle("h2", parent=S["Heading2"], fontSize=12, spaceBefore=12, spaceAfter=4)
MONO = ParagraphStyle("mono", parent=S["Normal"], fontName="Courier", fontSize=6.6, leading=8)



IMPLEMENTATION = [
    ("What ran, and what did not",
     "The benchmark has two tiers. The mechanical tier completed in full: 105 "
     "point-in-time cross-sections from January 2018 to August 2026, each a "
     "rebuild of Loom's factor layer as it would have stood on that month end, "
     "with no language model involved anywhere. The reader tier, which measures "
     "whether a person using Loom decides better, is quota-bound: the free model "
     "tier allows twenty calls per day per model, so it fills a few cells a night "
     "and is reported at whatever n it has reached."),

    ("Why the tiers are split",
     "Loom's document layer covers about forty companies and begins in October "
     "2025. Its quantitative layer covers hundreds of companies back to 2018. Any "
     "claim about regimes, sectors, size strata or multiple independent start "
     "dates can therefore only be made about the quantitative layer, and is made "
     "only about it here. The reader tier is confined to two 2026 decision dates "
     "in a rising market and cannot speak to bear-market behaviour at all."),

    ("The company panel, and why it is 400 and not 1,006",
     "Every cross-section is drawn from a fixed panel of the 400 largest companies "
     "by SEC rank that have both stored prices and stored fundamentals. The cap is "
     "a property of the hardware, not a judgement: this machine has 8GB of RAM, "
     "Docker holds 3.8GB of it and the application container already occupies "
     "2.2GB. Loading a thousand companies' bars and facts as ORM objects exhausted "
     "free memory and surfaced as 'could not read block 0 ... I/O error', which "
     "reads like database corruption and was not. At 400 companies peak resident "
     "memory is 1.12GB. This matters for one stated result: the small-cap stratum "
     "has only six usable cross-sections, so robustness across company size is "
     "weakly tested rather than tested."),

    ("How no-lookahead is enforced",
     "Three redundant layers, because a leak is invisible in the output and fatal "
     "to the result. First, at the source: findings are filtered by occurred_at, "
     "factor percentiles are recomputed by the point-in-time scorer rather than "
     "read from the stored table, briefs are rebuilt rather than reused, and "
     "prices are taken with a strictly-backwards on_or_before. Second, by "
     "provenance: every record that fed a packet is re-queried and its date "
     "asserted against the decision date, independently of the code that selected "
     "it. Third, by text: a regex sweep rejects any date later than the decision. "
     "Each layer is paired with a deliberate violation in gates.py that it must "
     "catch, so the audits are known to fire rather than assumed to."),

    ("The fundamentals are filing-dated, which was checked and not assumed",
     "The classic backtest leak is treating a fiscal period end as the moment the "
     "figure became public. Measured across 728,000 facts, 99.98 percent carry a "
     "positive lag between the filing date and the period end, mostly 31 to 60 "
     "days for quarterlies and over 120 for annual reports. Only 142 rows are "
     "zero-lag or negative. Rows with no period end are dropped by the adapter "
     "rather than defaulted, so the future-dated ones never enter scoring."),

    ("The portfolios, and what each one isolates",
     "control_equal holds the whole cross-section equally weighted and is the "
     "no-information baseline. control_random holds a random subset of the same "
     "size as Loom's book. loom_long holds the top quintile by Loom's production "
     "composite, equally weighted, so it differs from the control in selection "
     "only. loom_sized holds the entire universe tilted by percentile, so it "
     "differs in sizing only. That pair is the sizing-versus-selection "
     "decomposition and it needs no model call. loom_long_flat substitutes the "
     "plain average of percentiles for the production theme-weighted composite, "
     "which is how the theme weighting is attributed. Each individual factor also "
     "runs as its own long-short quintile book."),

    ("How the effects are decomposed",
     "Every name in the cross-section is classified by what Loom did to it and "
     "what then happened: captured_upside, added_loss, missed_upside, "
     "avoided_loss. Contributions are weighted by the change in exposure rather "
     "than by the raw return. An earlier unweighted version counted a name "
     "dropped from a 740-name control as heavily as a name held at a twentieth of "
     "the book, and the four effects summed to nothing recognisable; weighted, "
     "they reconcile to the actual difference between the two books to within a "
     "few basis points."),

    ("The statistics, written from scratch",
     "This machine has no numpy or scipy, so the statistics are pure Python and "
     "validated against known values: the Student-t p-values, computed through a "
     "continued-fraction incomplete beta rather than a normal approximation, match "
     "scipy to four decimal places. Two conventions are load-bearing. A statistic "
     "that cannot be computed returns None and never zero, because a zero "
     "t-statistic reads as 'measured, no effect' while an absent one means 'not "
     "measured'. And overlapping windows are never fed to a plain t-test: monthly "
     "observations of a 63- or 126-session holding period share quarters, so "
     "standard errors are Newey-West corrected with lags set to the overlap, and "
     "every headline is additionally re-tested on a non-overlapping subsample. "
     "Intervals come from a stationary block bootstrap. Families of comparisons "
     "carry a Benjamini-Hochberg correction."),

    ("Why the OLS column is printed at all",
     "To show how far it misleads. The 126-session alpha has a naive t of 4.97, an "
     "autocorrelation-robust t of 2.84, and a t of 1.70 once the overlap is "
     "removed. Reporting the first would have claimed a result that is not there, "
     "and the three columns are shown together so the reader can see the "
     "correction rather than take it on trust."),

    ("Pre-registration",
     "The primary endpoint, the secondary hypotheses and the declared limitations "
     "were written to PREREGISTRATION.md before any reader book was produced, so "
     "the endpoint could not be chosen after seeing results. Everything not named "
     "primary or secondary there is exploratory and is reported with the "
     "multiplicity correction applied."),
]


def fmt(x, d=3, w=0):
    if x is None:
        return "-"
    if isinstance(x, str):
        return x
    return f"{x:.{d}f}"


def table(rows, widths=None, size=7):
    t = Table(rows, colWidths=widths, repeatRows=1)
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), size),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5),
        ("TOPPADDING", (0, 0), (-1, -1), 2.5),
        ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.black),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f4f4f6")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def build() -> pathlib.Path:
    story = []
    story.append(Paragraph("Loom Benchmark B1", H1))
    story.append(Paragraph(
        f"Pre-registered multi-tier evaluation of the Loom equity research engine. "
        f"Generated {date.today().isoformat()}.", BODY))
    story.append(Spacer(1, 6))

    preregistration = (HERE / "PREREGISTRATION.md")
    if preregistration.exists():
        lim = preregistration.read_text().split("## 9. Declared limitations")[-1]
        story.append(Paragraph("Declared limitations, recorded before the run", H2))
        for line in lim.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            story.append(Paragraph(line.replace("**", ""), SMALL))

    story.append(PageBreak())
    story.append(Paragraph("How this was implemented", H1))
    for heading, text in IMPLEMENTATION:
        story.append(Paragraph(heading, H2))
        story.append(Paragraph(text, BODY))
    story.append(PageBreak())

    gates_log = HERE / "logs" / "gates.log"
    if gates_log.exists():
        story.append(Paragraph("Integrity gates", H2))
        rows = [["check", "result"]]
        for line in gates_log.read_text().splitlines():
            if line.startswith(("PASS", "FAIL", "WARN", "INFO")):
                kind, _, rest = line.partition(" ")
                rows.append([kind, Paragraph(rest, SMALL)])
        story.append(table(rows, widths=[38, 440]))

    quant = HERE / "results_quant.json"
    if quant.exists():
        q = json.loads(quant.read_text())
        story.append(PageBreak())
        story.append(Paragraph("Tier 1 — Loom's quantitative layer, measured mechanically", H2))
        story.append(Paragraph(
            f"{q['dates']} monthly point-in-time cross-sections. No language model is "
            f"involved in this tier: the factor layer is rebuilt as at each month end and "
            f"portfolios are formed from its output.", BODY))

        for horizon, block in q["horizons"].items():
            if not block.get("regimes"):
                continue
            ns = sorted(r["with_composite"] for r in block["regimes"])
            story.append(Paragraph(
                f"Horizon {horizon} sessions — {len(block['regimes'])} cross-sections, "
                f"median {ns[len(ns)//2]} companies ranked", H2))

            fam = {k: v["p"] for k, v in block["ic"].items()}
            adj = stats.benjamini_hochberg(fam)
            rows = [["signal", "n", "mean IC", "IR", "t", "p", "p (BH)", "share > 0"]]
            for k, v in sorted(block["ic"].items(), key=lambda kv: -abs(kv[1]["mean"] or 0)):
                pa, keep = adj.get(k, (None, False))
                rows.append([k + ("  *" if keep else ""), v["n"], fmt(v["mean"], 4),
                             fmt(v["ir"], 2), fmt(v["t"], 2), fmt(v["p"], 4),
                             fmt(pa, 4), fmt(v["share_positive"], 2)])
            story.append(KeepTogether([
                Paragraph("Information coefficient: rank correlation between the signal and "
                          "forward return in excess of QQQ. Newey-West t, corrected for the "
                          "overlap that monthly observations of a multi-month holding period "
                          "create. Starred rows survive Benjamini-Hochberg at 0.05.", SMALL),
                Spacer(1, 3), table(rows)]))

            rows = [["book", "n", "excess", "t", "p", "vol", "maxDD", "Sharpe",
                     "hit", "HHI", "names"]]
            for k, v in sorted(block["books"].items()):
                if not v.get("n") or k.startswith("factor::"):
                    continue
                rows.append([k, v["n"], fmt(v["mean_excess"], 4), fmt(v["t"], 2),
                             fmt(v["p"], 4), fmt(v["volatility"], 3),
                             fmt(v["max_drawdown"], 3), fmt(v["sharpe"], 2),
                             fmt(v["hit_rate"], 2), fmt(v["hhi"], 3),
                             fmt(v["n_names"], 1)])
            story.append(Spacer(1, 8))
            story.append(KeepTogether([
                Paragraph("Portfolios. Excess is over QQQ for the same window. "
                          "loom_sized holds the whole universe tilted by percentile and so "
                          "differs from the control only in sizing; loom_long differs only in "
                          "selection.", SMALL),
                Spacer(1, 3), table(rows)]))

            ds = sorted(block["deciles"].items(), key=lambda kv: int(kv[0]))
            rows = [["composite decile", "n", "mean forward excess"]]
            for d, v in ds:
                rows.append([d, v["n"], f"{100*(v['mean_excess'] or 0):+.3f}%"])
            mono = stats.spearman([int(k) for k, _ in ds],
                                  [v["mean_excess"] or 0 for _, v in ds])
            story.append(Spacer(1, 8))
            story.append(KeepTogether([
                Paragraph(f"Calibration of the composite. Monotonicity (Spearman of decile "
                          f"against mean return): {fmt(mono, 3)}. A calibrated score rises "
                          f"monotonically across deciles.", SMALL),
                Spacer(1, 3), table(rows, widths=[90, 60, 120])]))

            rows = [["stratum", "n", "mean IC", "t", "p"]]
            for k, v in sorted(block["strata_ic"].items()):
                rows.append([k, v["n"], fmt(v["mean"], 4), fmt(v["t"], 2), fmt(v["p"], 4)])
            for k, v in sorted(block["sector_ic"].items(), key=lambda kv: -kv[1]["n"])[:12]:
                rows.append([k, v["n"], fmt(v["mean"], 4), fmt(v["t"], 2), fmt(v["p"], 4)])
            story.append(Spacer(1, 8))
            story.append(KeepTogether([
                Paragraph("Universe robustness: the same measurement inside size strata "
                          "and, below the rule, inside sectors.", SMALL),
                Spacer(1, 3), table(rows)]))

            rows = [["effect", "n", "total (pts)", "mean"]]
            for k, v in block["effects"].items():
                rows.append([k, v["n"], f"{100*v['total']:+.1f}",
                             f"{100*(v['mean'] or 0):+.2f}%"])
            story.append(Spacer(1, 8))
            story.append(table(rows, widths=[110, 60, 80, 70]))
            story.append(PageBreak())

    if quant.exists():
        q2 = json.loads(quant.read_text())
        story.append(Paragraph("Paired against the control, split by what the market did", H2))
        story.append(Paragraph(
            "loom_long minus control_equal on identical dates, so the market is "
            "differenced out. Cross-sections are split into terciles by the "
            "benchmark's realised return over the holding period. The regime is "
            "assigned after the fact, so this characterises the book's exposure "
            "rather than describing a tradeable rule.", SMALL))
        rows = [["horizon", "regime", "n", "benchmark range", "loom_long - control", "t", "p"]]
        for horizon, block in q2["horizons"].items():
            regimes = block.get("regimes") or []
            raw = block.get("books_raw") or {}
            if not regimes or "control_equal" not in raw or "loom_long" not in raw:
                continue
            reg = {r["as_of"]: r["bench"] for r in regimes}
            order = sorted(reg, key=lambda d: reg[d])
            third = max(1, len(order) // 3)
            groups = (("falling", order[:third]), ("flat", order[third:-third]),
                      ("rising", order[-third:]))
            ctrl = {r["as_of"]: r for r in raw["control_equal"]}
            for label, dates in groups:
                sel = set(dates)
                d = [r["excess"] - ctrl[r["as_of"]]["excess"] for r in raw["loom_long"]
                     if r["as_of"] in sel and r["as_of"] in ctrl]
                if len(d) < 3:
                    continue
                t, pv = stats.newey_west_t(d, max(1, round(int(horizon) / 21)))
                brange = f"{100*min(reg[x] for x in dates):+.0f}% to {100*max(reg[x] for x in dates):+.0f}%"
                rows.append([horizon, label, len(d), brange,
                             f"{100*stats.mean(d):+.2f} pts", fmt(t, 2), fmt(pv, 4)])
        story.append(Spacer(1, 3))
        story.append(table(rows))
        story.append(Spacer(1, 10))

    # The decisive table: Loom's book is 20% less market-exposed, so its
    # downturn advantage has to be tested against that exposure, not against
    # zero. Three standard errors per row because the windows overlap.
    try:
        a = alpha_mod.run()
    except Exception:
        a = None
    if a and a["rows"]:
        story.append(Paragraph("Beta-adjusted alpha against the equal-weight control", H2))
        story.append(Paragraph(
            "Loom's top-quintile book beat an equal-weight control in every falling "
            "market and lost in every rising one, at all three horizons. That is what "
            "a lower-beta portfolio does mechanically, so the question is whether "
            "anything survives adjusting for the exposure. Monthly observations of a "
            "multi-month hold overlap, so the OLS column is reported only to show how "
            "far it overstates: 4.97 against 2.84 once corrected and 1.70 on "
            "non-overlapping data. Starred rows survive Benjamini-Hochberg over all "
            "twelve tests.", SMALL))
        rows = [["horizon", "book", "n", "alpha", "beta", "t OLS", "t NW", "p NW",
                 "n non-ov", "t non-ov", "p non-ov", "p (BH)"]]
        for row in sorted(a["rows"], key=lambda x: (x["horizon"], x["arm"])):
            rows.append([row["horizon"], row["arm"] + ("  *" if row["survives_bh"] else ""),
                         row["n"], f"{100*row['alpha']:+.3f}%", fmt(row["beta"], 3),
                         fmt(row["t_ols"], 2), fmt(row["t_nw"], 2), fmt(row["p_nw"], 4),
                         row["n_nonoverlap"], fmt(row["t_nonoverlap"], 2),
                         fmt(row["p_nonoverlap"], 4), fmt(row["p_bh"], 4)])
        story.append(Spacer(1, 3))
        story.append(table(rows, size=6.6))
        story.append(PageBreak())

    db = SessionLocal()
    r = reader_metrics.run(db)
    story.append(Paragraph("Tier 2 — reader books", H2))
    if not r.get("books"):
        story.append(Paragraph(
            "No reader books were scored. The free model tier allows twenty "
            "generate_content calls per day per model, so this tier fills a few cells "
            "per night rather than in one run.", BODY))
    else:
        story.append(Paragraph(
            f"{r['books']} books scored. Every contrast is paired inside a "
            f"(persona, seed, decision date) cell; nothing is averaged across unpaired "
            f"cells.", BODY))
        rows = [["arm vs control", "metric", "n", "mean", "t", "p", "CI low", "CI high",
                 "n positive"]]
        for arm, block in sorted(r["arms"].items()):
            for metric, v in block.items():
                rows.append([arm, metric, v["n"], fmt(v["mean"], 4), fmt(v["t"], 2),
                             fmt(v["p"], 4), fmt(v["ci_low"], 4), fmt(v["ci_high"], 4),
                             v["positive"]])
        story.append(table(rows))

        rows = [["arm", "books", "citations", "verbatim rate", "positions citing"]]
        for arm, v in sorted(r["grounding"].items()):
            rows.append([arm, v["books"], v["citations"],
                         fmt(v["grounding_rate"], 3), fmt(v["citation_rate"], 3)])
        story.append(Spacer(1, 8))
        story.append(KeepTogether([
            Paragraph("Evidence utilisation. A citation counts as grounded only if it "
                      "appears verbatim in that book's own packet; an ungrounded citation "
                      "is a confabulation and cannot be read as use of the research.", SMALL),
            Spacer(1, 3), table(rows)]))

        rows = [["arm", "n", "Brier", "ECE", "discrimination"]]
        for arm, v in sorted(r["calibration"].items()):
            if v:
                rows.append([arm, v["n"], fmt(v["brier"], 4), fmt(v["ece"], 4),
                             fmt(v["discrimination"], 3)])
        if len(rows) > 1:
            story.append(Spacer(1, 8))
            story.append(KeepTogether([
                Paragraph("Calibration of stated conviction against realised outcome.", SMALL),
                Spacer(1, 3), table(rows)]))

        rows = [["arm", "latency s", "input tokens", "output tokens"]]
        for arm, v in sorted(r["efficiency"].items()):
            rows.append([arm, fmt(v["latency"], 1), fmt(v["input_tokens"], 0),
                         fmt(v["output_tokens"], 0)])
        story.append(Spacer(1, 8))
        story.append(KeepTogether([
            Paragraph("Research efficiency.", SMALL), Spacer(1, 3), table(rows)]))

        (HERE / "results_reader.json").write_text(json.dumps(r, indent=1, default=str))

    out = HERE / f"loom_benchmark_b1_{date.today().isoformat()}.pdf"
    SimpleDocTemplate(str(out), pagesize=A4, leftMargin=16 * mm, rightMargin=16 * mm,
                      topMargin=16 * mm, bottomMargin=16 * mm,
                      title="Loom Benchmark B1").build(story)
    print(f"wrote {out}")
    return out


if __name__ == "__main__":
    build()
