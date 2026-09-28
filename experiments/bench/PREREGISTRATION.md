# Loom Benchmark B1 — pre-registration

Written and hashed **before** any reader agent runs. The point is that the
primary endpoint cannot be chosen after seeing the results. Everything not
named primary or secondary below is exploratory and reported with a
Benjamini-Hochberg correction across the exploratory family.

Author: automated run. Date written: 2026-09-28.

---

## 1. What is being tested

Loom is an equity research engine. It reads filings, extracts findings,
measures each finding against what documents of that genre normally say,
ranks companies on factors rebuilt point-in-time, and issues a verdict.

The question is not whether Loom predicts returns. It is whether **a reader
who has Loom makes better decisions than the same reader without it**, and
which part of Loom is responsible.

The unit of observation is a **book**: one reader agent, one persona, one
seed, one decision date, one arm, allocating across one universe.

## 2. Arms

Every arm sees the same universe, the same decision date, and the same
price history. They differ only in what research is attached.

| arm | contents |
|---|---|
| `control` | ticker, name, sector, last close, price standing. No Loom. |
| `evidence` | control + Loom's findings, contradictions, factor percentiles. **No verdict.** |
| `full` | `evidence` + Loom's verdict and headline. |
| `verdict_only` | control + verdict and headline, **no underlying evidence**. |
| `placebo` | structurally identical to `evidence`, with each company's findings **reassigned to a different company** by a fixed permutation. |
| `no_factor` | `evidence` minus factor percentiles. |
| `no_price` | `evidence` minus price context. |
| `no_contradiction` | `evidence` minus the contradiction block. |
| `degraded` | only stale, low-confidence, or routine findings. Tests abstention. |
| `quant_loom` | control + factor percentiles only (the layer that exists before 2025-10). Tier 2 only. |

`placebo` is the arm that decides whether Loom's *content* matters or only its
*format*. If `placebo` performs like `evidence`, the effect is presentation.

## 3. Primary endpoint

**H1.** The paired difference in **3-month benchmark-adjusted total return**
between `full` and `control`, paired within each (persona, seed, decision
date) cell, over the Tier 1 grid.

- Benchmark: QQQ total return over the identical window.
- Estimator: mean paired difference.
- Interval: stationary block bootstrap (expected block length 5 sessions,
  10,000 resamples) over the daily portfolio return series, resampling
  whole cells to respect the pairing.
- Declared direction: two-sided. Loom is not assumed to help.
- Success: 95% interval excluding zero.

**H2 (co-primary, risk).** The paired difference in **maximum drawdown**
between `full` and `control`, same pairing and interval method.

A result where H1 is null and H2 is positive is a real and reportable
outcome: it would mean Loom is a risk control rather than a return source.

## 4. Secondary endpoints

**H3 (verdict contribution).** `full` minus `evidence` on the H1 metric.
Isolates the verdict given the same underlying research.

**H4 (content vs format).** `evidence` minus `placebo` on the H1 metric. A
null here invalidates any positive reading of H2 and H3, because it would
mean the arms differ in style rather than information.

**H5 (reader interaction).** The persona × arm interaction on the H1 metric.

## 5. Diagnostics that gate interpretation

These are computed and reported **before** outcomes, and they can void an
otherwise significant result.

- **Verdict entropy.** Shannon entropy of the verdict distribution in each
  `full` packet. Near-zero entropy means the verdict carries no
  discriminating information, and any `full` effect must be described as the
  effect of a uniform signal, not of Loom's judgement.
- **Provenance audit.** Every record that feeds a packet must carry a date
  at or before the decision date. Checked against source rows, not text.
- **Leak canary.** A packet with a deliberately future-dated record must be
  rejected by the audit. If the canary is not caught, the run is void.
- **Evidence grounding.** Fraction of each agent's cited snippets that appear
  verbatim in its own packet. Low grounding means the agent is confabulating
  and its rationales cannot be read as use of Loom.

## 6. Measures

Decision quality, raw return, benchmark-adjusted return, volatility,
downside deviation, max drawdown, Sharpe, Sortino, hit rate, HHI, maximum
single-name weight, sector HHI, mean pairwise correlation of held names,
cash share, gross and net exposure, short count.

Paired per-decision effects classified as `missed_upside`, `avoided_loss`,
`captured_upside`, `added_loss`, `unchanged`.

**Sizing versus selection.** Two synthetic books per treatment book, needing
no agent: (a) treatment's ticker set at control's weighting scheme, isolating
selection; (b) control's ticker set at treatment's weights, isolating sizing.

**Calibration.** Agent conviction against realised outcome: reliability
curve, Brier score, expected calibration error. Separately, Loom's own
stance strength against realised outcome.

**Thesis accuracy.** For a thesis stated at date D, the direction of findings
occurring in (D, D+90d] for that company, compared mechanically against the
thesis direction. No LLM judge.

**Consistency.** Across seeds on an identical packet: action agreement rate
and Spearman correlation of weights.

**Research efficiency.** Input tokens, output tokens, and wall-clock latency
per book.

## 7. Tier 1 — depth

Decision dates `2026-03-02`, `2026-06-25`, `2026-09-01`. Horizons of 21 and
63 sessions where the price history allows, 21 only for the last date.

Universe: 36 companies, deliberately stratified — 24 with at least three
findings visible at the decision date, 12 with none. The second stratum is
the test of whether Loom helps where it has not read anything.

Grid: 3 core arms x 4 personas x 3 seeds x 3 dates = 108 books.
Mechanism arms: a reduced design, about 50 books.

## 8. Tier 2 — breadth

Loom's document layer does not exist before 2025-10. Its quantitative layer
does, back to 2020. Tier 2 therefore tests `quant_loom` against `control`
only, over 8 decision dates from 2020 to 2025 covering the COVID collapse,
the 2022 bear market, and two bull stretches, on 60-name universes drawn from
mega, mid and small strata.

This is the only part of the benchmark that sees a bear market, and it tests
**less than half of Loom**. Stated plainly rather than generalised.

## 9. Declared limitations

These are limitations of the data, not of the analysis, and they are recorded
now so they cannot be presented later as findings.

1. **Regime coverage is bull-only for document Loom.** All three Tier 1 dates
   sit in a rising 2026 market. Bear-market behaviour of the verdict is
   untested and untestable with this corpus.
2. **The three Tier 1 dates overlap.** They are not independent draws. The
   block bootstrap widens intervals but cannot manufacture independence.
   Effective sample size is closer to two than three.
3. **Findings exist for about 40 companies.** Not 1,006. The wide coverage is
   prices and fundamentals.
4. **The reader is Gemini 3.6 Flash, the same model family as Loom's own
   extraction.** A shared-model confound. Partly probed by rerunning a subset
   on 3.5 Flash, which is a weak proxy for a different reasoning style.
5. **Human transferability is not tested.** No humans are in this run. A
   blinded human protocol is produced as an artefact, unrun. Any claim that
   these effects transfer to human readers is unsupported by this benchmark.
6. **Multiplicity.** Roughly 30 exploratory comparisons. BH-corrected, and
   single uncorrected contrasts are not to be read as findings.
