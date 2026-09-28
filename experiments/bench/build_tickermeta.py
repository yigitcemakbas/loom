"""Per-ticker, per-date metadata the anchoring test needs.

The anchoring question is not "did the reader agree with the verdict" but "did
the reader follow the verdict *where the underlying evidence pointed the other
way*". Answering it needs the verdict and the evidence direction as two separate
numbers, and the packet only carries the verdict, so they are recomputed here
under the same point-in-time discipline: norms from findings on or before D, the
brief rebuilt with now=D.
"""
from __future__ import annotations

import json
import pathlib
from collections import defaultdict
from datetime import datetime, timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.engine.brief import build_brief
from app.engine.disclosure import measure_norms
from app.models.company import Company
from app.models.signal import Signal

import spec

OUT = pathlib.Path(__file__).parent / "tickermeta"

STANCE_SIGN = {
    "strong_negative": -1, "negative": -1, "mixed": 0,
    "positive": 1, "strong_positive": 1, "insufficient": 0, "quiet": 0,
}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    db = SessionLocal()
    companies = {c.ticker: c for c in db.execute(select(Company)).scalars()}
    sectors = {str(c.id): c.sector for c in companies.values() if c.sector}
    all_signals = list(db.execute(
        select(Signal).where(Signal.dismissed_at.is_(None))).scalars())

    for w in spec.READER_WINDOWS:
        cutoff = datetime.combine(w.decide, datetime.max.time(), timezone.utc)
        past = [s for s in all_signals if s.occurred_at <= cutoff]
        norms = measure_norms(past, sectors)
        by_company = defaultdict(list)
        for s in past:
            by_company[s.company_id].append(s)

        rows = {}
        for ticker, company in companies.items():
            sigs = by_company.get(company.id, [])
            if not sigs:
                continue
            brief = build_brief(
                sigs, now=datetime.combine(w.decide, datetime.min.time(), timezone.utc),
                norms=norms)
            ev = brief.evidence or {}
            rows[ticker] = {
                "stance": brief.stance.value,
                "verdict_sign": STANCE_SIGN.get(brief.stance.value, 0),
                # The direction the evidence itself leans, before the
                # sufficiency gate decides whether a verdict may be stated.
                # This is what makes the anchoring test possible: a reader can
                # be shown "insufficient" over evidence that leans clearly.
                "evidence_mean": ev.get("excess_mean"),
                "evidence_sign": (
                    0 if ev.get("excess_mean") in (None, 0)
                    else (1 if ev["excess_mean"] > 0 else -1)),
                "confidence": brief.confidence,
                "evidence_strength": ev.get("evidence_strength"),
                "informative_count": ev.get("informative_count"),
                "documents_read": ev.get("documents_read"),
                "counts": ev.get("counts"),
                "n_findings": brief.signal_count,
            }
        (OUT / f"{w.decide}.json").write_text(json.dumps(rows, indent=1))
        disagree = sum(1 for r in rows.values()
                       if r["verdict_sign"] and r["evidence_sign"]
                       and r["verdict_sign"] != r["evidence_sign"])
        stated = sum(1 for r in rows.values() if r["verdict_sign"])
        silent_but_leaning = sum(1 for r in rows.values()
                                 if not r["verdict_sign"] and r["evidence_sign"])
        print(f"{w.decide}  {len(rows):3d} tickers  verdict stated {stated:3d}  "
              f"verdict silent while evidence leans {silent_but_leaning:3d}  "
              f"verdict vs evidence disagree {disagree:3d}", flush=True)


if __name__ == "__main__":
    main()
