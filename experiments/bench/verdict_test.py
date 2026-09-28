"""Does the verdict predict anything, and are the new verdicts worth having?

The sufficiency fix took Loom's abstention rate from 97.5% to 22%, which made
the verdict vary. It did not show that the verdicts are any good, and the
obvious way for that change to be wrong is that it converted honest silence into
noise. So this splits the verdicts into two groups and tests them separately:

  established   verdicts the old gate would also have issued (strength >= 2.0)
  new           verdicts only the fixed gate issues (>= 2 informative findings
                but strength below the old bar)

If the `new` group has no predictive power, the fix traded silence for guessing
and should be reconsidered. Both groups are tested two independent ways, neither
of which needs a language model:

  evidence   does the direction stated at D match the direction of findings
             that arrive in (D, D+90 days]? Loom's own later reading of the
             company, held out at the time the verdict was formed.
  price      does the stance predict benchmark-adjusted return over the next
             21 or 63 sessions?

Briefs are rebuilt point-in-time at every date: norms measured from findings on
or before D, and the brief built with `now=D`. Nothing dated after D is visible.
"""
from __future__ import annotations

import json
import pathlib
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select

from app.db.session import SessionLocal
from app.engine.brief import build_brief
from app.engine.disclosure import measure_norms
from app.models.brief import Stance
from app.models.company import Company
from app.models.signal import Signal

import quantstudy
import stats

HERE = pathlib.Path(__file__).parent

# The old bar, kept as a constant so the comparison is explicit rather than
# implied: this is the value the previous gate compared a weighted strength
# against, and every verdict below it is one the old engine refused.
OLD_STRENGTH_BAR = 2.0
MIN_FINDINGS = 3
EVIDENCE_HORIZON_DAYS = 90

# Monthly decision dates. Findings cluster in 2026-02 and 2026-07/08, so these
# span both clusters. The windows overlap, which the statistics account for.
DATES = [date(2026, m, 1) for m in range(2, 9)]

SCORE = {
    Stance.STRONG_NEGATIVE: -2.0, Stance.NEGATIVE: -1.0, Stance.MIXED: 0.0,
    Stance.POSITIVE: 1.0, Stance.STRONG_POSITIVE: 2.0,
}


def _sign(x: Optional[float]) -> int:
    if x is None or abs(x) < 1e-9:
        return 0
    return 1 if x > 0 else -1


def run(db) -> dict:
    tape = quantstudy.Tape(db)
    companies = {c.ticker: c for c in db.execute(select(Company)).scalars()}
    all_signals = list(db.execute(
        select(Signal).where(Signal.dismissed_at.is_(None))).scalars())
    by_company: dict[object, list[Signal]] = defaultdict(list)
    for s in all_signals:
        by_company[s.company_id].append(s)
    sectors = {str(c.id): c.sector for c in companies.values() if c.sector}

    observations: list[dict] = []
    for as_of in DATES:
        cutoff = datetime.combine(as_of, datetime.max.time(), timezone.utc)
        past = [s for s in all_signals if s.occurred_at <= cutoff]
        if not past:
            continue
        norms = measure_norms(past, sectors)
        past_by_company: dict[object, list[Signal]] = defaultdict(list)
        for s in past:
            past_by_company[s.company_id].append(s)

        for ticker, company in companies.items():
            sigs = past_by_company.get(company.id, [])
            if len(sigs) < MIN_FINDINGS:
                continue
            brief = build_brief(
                sigs, now=datetime.combine(as_of, datetime.min.time(), timezone.utc),
                norms=norms)
            if brief.stance not in SCORE:
                continue                      # insufficient or quiet: no claim made
            strength = (brief.evidence or {}).get("evidence_strength")
            n_inf = (brief.evidence or {}).get("informative_count")

            # Held-out evidence: what Loom read about this company afterwards.
            later = [s for s in by_company[company.id]
                     if cutoff < s.occurred_at
                     <= datetime.combine(as_of + timedelta(days=EVIDENCE_HORIZON_DAYS),
                                         datetime.max.time(), timezone.utc)]
            # Compared in the stance's own units, not in raw sentiment.
            #
            # The stance is a residual: how far this company's disclosure departs
            # from what documents of its kind normally carry. Raw subsequent
            # sentiment is a level. A company can legitimately read "better than
            # usual" while the raw tone of its next filing is negative, because
            # every annual report's risk section is negative, and scoring the
            # verdict against the level would count that as a miss. That
            # mismatch is the whole reason the genre correction exists, and
            # measuring against it would have manufactured a null.
            later_dir = None
            if later:
                excesses = [norms.excess_for(s) for s in later]
                excesses = [e for e in excesses if e is not None]
                if excesses:
                    later_dir = sum(excesses) / len(excesses)
                else:
                    tones = [s.sentiment_score for s in later
                             if s.sentiment_score is not None]
                    if tones:
                        later_dir = sum(tones) / len(tones)

            row = {
                "as_of": as_of.isoformat(), "ticker": ticker,
                "stance": brief.stance.value, "score": SCORE[brief.stance],
                "confidence": brief.confidence,
                "strength": strength, "informative_count": n_inf,
                "group": ("established" if (strength or 0) >= OLD_STRENGTH_BAR else "new"),
                "later_findings": len(later), "later_direction": later_dir,
            }
            for horizon in (21, 63):
                win = tape.window(as_of, horizon)
                bench = tape.total(quantstudy.BENCHMARK, win) if win else None
                r = tape.total(ticker, win) if win else None
                row[f"excess_{horizon}"] = (r - bench) if (r is not None and bench is not None) else None
            observations.append(row)

    return {"observations": observations, "summary": _summarise(observations)}


def _summarise(obs: list[dict]) -> dict:
    out: dict = {}
    for group in ("all", "established", "new"):
        sel = obs if group == "all" else [o for o in obs if o["group"] == group]
        if not sel:
            continue
        block: dict = {"n": len(sel),
                       "verdicts": {s: sum(1 for o in sel if o["stance"] == s)
                                    for s in sorted({o["stance"] for o in sel})}}

        # Agreement with the evidence that arrived afterwards.
        paired = [o for o in sel if o["later_direction"] is not None
                  and _sign(o["score"]) != 0]
        if paired:
            agree = sum(1 for o in paired
                        if _sign(o["score"]) == _sign(o["later_direction"]))
            rho = stats.spearman([o["score"] for o in paired],
                                 [o["later_direction"] for o in paired])
            block["evidence"] = {
                "n": len(paired), "agreement": agree / len(paired),
                "spearman": rho,
                "p_vs_coinflip": _binomial_p(agree, len(paired)),
            }

        # Agreement with what the price then did.
        for horizon in (21, 63):
            have = [o for o in sel if o.get(f"excess_{horizon}") is not None
                    and _sign(o["score"]) != 0]
            if len(have) < 10:
                continue
            rho = stats.spearman([o["score"] for o in have],
                                 [o[f"excess_{horizon}"] for o in have])
            longs = [o[f"excess_{horizon}"] for o in have if o["score"] > 0]
            shorts = [o[f"excess_{horizon}"] for o in have if o["score"] < 0]
            spread = None
            if longs and shorts:
                spread = stats.mean(longs) - stats.mean(shorts)
            block[f"price_{horizon}"] = {
                "n": len(have), "spearman": rho,
                "mean_excess_positive": stats.mean(longs) if longs else None,
                "mean_excess_negative": stats.mean(shorts) if shorts else None,
                "spread": spread,
            }
        out[group] = block
    return out


def _binomial_p(successes: int, n: int) -> Optional[float]:
    """Two-sided exact binomial against a coin flip. Small n here, so the normal
    approximation would be the wrong tool."""
    if n == 0:
        return None
    from math import comb
    def pmf(k): return comb(n, k) * 0.5 ** n
    observed = pmf(successes)
    return min(1.0, sum(pmf(k) for k in range(n + 1) if pmf(k) <= observed + 1e-15))


if __name__ == "__main__":
    db = SessionLocal()
    result = run(db)
    (HERE / "results_verdict.json").write_text(json.dumps(result, indent=1, default=str))
    s = result["summary"]
    print(f"{len(result['observations'])} directional verdicts across {len(DATES)} dates\n")
    for group in ("all", "established", "new"):
        b = s.get(group)
        if not b:
            continue
        print(f"=== {group}  (n={b['n']}) ===")
        print(f"    verdicts: {b['verdicts']}")
        ev = b.get("evidence")
        if ev:
            print(f"    vs later findings : n={ev['n']:3d} agreement {100*ev['agreement']:.1f}% "
                  f"rho={ev['spearman']} p={ev['p_vs_coinflip']}")
        for h in (21, 63):
            pr = b.get(f"price_{h}")
            if pr:
                sp = pr["spread"]
                print(f"    vs price {h:3d}d      : n={pr['n']:3d} rho={pr['spearman']} "
                      f"spread={100*sp:+.2f} pts" if sp is not None else
                      f"    vs price {h:3d}d      : n={pr['n']:3d} rho={pr['spearman']}")
        print()
