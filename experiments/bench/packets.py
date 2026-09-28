"""Building one research packet per arm, and proving it contains no future.

The no-lookahead rule is enforced three ways, deliberately redundant because a
leak is invisible in the output and fatal to the result:

  1. at the source     findings filtered by occurred_at, factors recomputed by
                       the point-in-time scorer, prices taken on_or_before
  2. by provenance     every record that fed the packet is re-queried and its
                       date asserted against the decision date
  3. by text           a regex sweep for any date later than the decision

A canary in gates.py checks that (2) and (3) actually fire, because an audit
nobody has seen fail is an assumption.
"""
from __future__ import annotations

import hashlib
import random
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from sqlalchemy import select, text as sqltext

from app.engine.brief import build_brief
from app.engine.contradiction import find_contradictions
from app.engine.disclosure import measure_norms
from app.engine.price_context import standing
from app.engine.price_loader import load_history
from app.engine.quant.runner import score_universe
from app.engine.quant.factors import FACTORS_BY_KEY
from app.models.company import Company
from app.models.signal import Signal

import spec


class LookaheadError(AssertionError):
    """Raised instead of shipping a packet that knows the future."""


# --------------------------------------------------------------- universes


def _findings_before(db, as_of: date) -> dict[str, int]:
    """How many findings each company had at the decision date."""
    rows = db.execute(sqltext("""
        select c.ticker, count(*) n from signals s join companies c on c.id = s.company_id
        where s.occurred_at <= :cut and s.dismissed_at is null group by c.ticker
    """), {"cut": datetime.combine(as_of, datetime.max.time(), timezone.utc)}).all()
    return {r.ticker: r.n for r in rows}


def tier1_universe(db, as_of: date) -> tuple[list[str], list[str]]:
    """Twenty-four companies Loom has read, twelve it has not.

    The second stratum is not padding. Loom has read about forty companies and
    priced a thousand, so the common case for a user is a company Loom has
    nothing to say about, and an experiment run only on the covered names would
    measure Loom at its best and call it Loom.
    """
    counts = _findings_before(db, as_of)
    priced = {r.ticker for r in db.execute(sqltext("""
        select distinct c.ticker from companies c join price_bars p on p.company_id = c.id
        where p.session_date <= :d group by c.ticker having count(*) > 250
    """), {"d": as_of}).all()}

    covered = sorted((t for t, n in counts.items()
                      if n >= spec.TIER1_MIN_FINDINGS and t in priced),
                     key=lambda t: (-counts[t], t))[:spec.TIER1_COVERED]

    rows = db.execute(sqltext("""
        select ticker from companies where sec_rank is not null order by sec_rank
    """)).all()
    uncovered = [r.ticker for r in rows
                 if r.ticker not in counts and r.ticker in priced][:spec.TIER1_UNCOVERED]
    return covered, uncovered


def tier2_universe(db, as_of: date, seed: int) -> list[str]:
    """Sixty companies across size strata, drawn deterministically.

    Stratified rather than taken from the top, because a universe of mega-caps
    tests Loom on the companies with the most analyst coverage already and the
    least room for a research product to add anything.
    """
    rows = db.execute(sqltext("""
        select c.ticker, c.sec_rank from companies c
        where c.sec_rank is not null
          and exists (select 1 from price_bars p where p.company_id = c.id
                      and p.session_date <= :d)
        order by c.sec_rank
    """), {"d": as_of}).all()
    ranked = [r.ticker for r in rows]
    if not ranked:
        return []
    strata = [ranked[:150], ranked[150:450], ranked[450:]]
    rnd = random.Random(f"tier2-{as_of}-{seed}")
    out: list[str] = []
    per = spec.TIER2_SIZE // 3
    for s in strata:
        out.extend(rnd.sample(s, min(per, len(s))))
    return sorted(set(out))


# --------------------------------------------------------------- provenance


@dataclass
class Provenance:
    """What the packet was built from, so the claim of no lookahead is checkable
    against rows rather than taken on faith."""
    as_of: date
    tickers: list[str]
    signal_ids: list[str] = field(default_factory=list)
    newest_signal: Optional[date] = None
    newest_bar: Optional[date] = None
    newest_fact_filed: Optional[date] = None
    verdicts: dict[str, str] = field(default_factory=dict)

    def audit(self, db) -> None:
        for label, seen in (("finding", self.newest_signal),
                            ("price bar", self.newest_bar),
                            ("filed fact", self.newest_fact_filed)):
            if seen is not None and seen > self.as_of:
                raise LookaheadError(
                    f"{label} dated {seen} is after the decision date {self.as_of}")
        # The scorer's own filter, re-applied independently. If score_universe
        # ever loosened its narrowing this is what would catch it.
        if self.tickers:
            row = db.execute(sqltext("""
                select max(f.as_of_date) d from structured_facts f
                join companies c on c.id = f.company_id
                where c.ticker = any(:ts) and f.as_of_date <= :cut
            """), {"ts": list(self.tickers), "cut": self.as_of}).first()
            if row and row.d and row.d > self.as_of:
                raise LookaheadError(f"fact filed {row.d} after {self.as_of}")


_DATE = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")


def text_audit(packets: dict[str, str], as_of: date) -> None:
    for arm, body in packets.items():
        for m in _DATE.finditer(body):
            found = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if found > as_of:
                raise LookaheadError(f"{arm} packet leaks {found}, after {as_of}")
    if "evidence" in packets and "LOOM VERDICT" in packets["evidence"]:
        raise LookaheadError("evidence arm contains the verdict it exists to exclude")
    if "full" in packets and "LOOM VERDICT" not in packets["full"]:
        raise LookaheadError("full arm is missing the verdict")


# --------------------------------------------------------------- formatting


def _fmt_factors(pct: dict, limit: int = 8) -> list[str]:
    ranked = sorted([(k, v) for k, v in pct.items()
                     if v is not None and k != "composite"],
                    key=lambda kv: abs(kv[1] - 0.5), reverse=True)[:limit]
    out = []
    for k, v in ranked:
        f = FACTORS_BY_KEY.get(k)
        out.append(f"  {(f.label if f else k):32s} {v:.2f}")
    return out


# The boilerplate genres. A risk factor and a quoted sentence are what every
# filing carries whether or not anything happened; an anomaly or a guidance
# change is what only some filings carry.
_BOILERPLATE = {"NEW_RISK_FACTOR", "NOTABLE_QUOTE"}


def _degrade(sigs: list[Signal]) -> list[Signal]:
    """The weakest half of one company's evidence, for the degraded arm.

    Defined relative to the company rather than by an absolute threshold. An
    absolute cut was tried first and kept everything: Loom's priority scores
    average 0.24 and not one finding on any covered company exceeds 0.35, so a
    fixed bar makes the degraded arm byte-identical to the real one. That the
    fixed bar failed is a fact about the corpus, recorded in the diagnostics.
    """
    if not sigs:
        return []
    ranked = sorted(sigs, key=lambda s: (s.priority or 0))
    keep = ranked[: max(1, len(ranked) // 2)]
    boiler = [s for s in keep if str(getattr(s.signal_type, "name", s.signal_type)) in _BOILERPLATE]
    return boiler or keep


# --------------------------------------------------------------- the builder


def build(db, *, as_of: date, tier: int, seed_for_universe: int = 1,
          arms: tuple[str, ...] = spec.ALL_ARMS) -> tuple[dict[str, str], Provenance]:
    """One packet per requested arm, all from the same universe and date."""
    cutoff = datetime.combine(as_of, datetime.max.time(), timezone.utc)

    if tier == 1:
        covered, uncovered = tier1_universe(db, as_of)
        tickers = covered + uncovered
    else:
        tickers = tier2_universe(db, as_of, seed_for_universe)

    companies = {c.ticker: c for c in db.execute(
        select(Company).where(Company.ticker.in_(tickers))).scalars()}
    tickers = [t for t in tickers if t in companies]

    past = list(db.execute(select(Signal).where(
        Signal.occurred_at <= cutoff, Signal.dismissed_at.is_(None))).scalars())
    sectors = {str(c.id): c.sector for c in companies.values() if c.sector}
    norms = measure_norms(past, sectors)

    by_company: dict[object, list[Signal]] = {}
    for s in past:
        by_company.setdefault(s.company_id, []).append(s)

    # Ranked against the whole four-hundred-company panel, not against the sixty
    # names in the packet. A percentile means "against comparable companies", so
    # ranking inside the experiment's own sample would show the reader a
    # different number from the one Loom produces. The panel rather than all
    # thousand companies because the 8GB host cannot hold a thousand companies'
    # bars and facts at once, and because the mechanical tier uses the same
    # panel, which keeps the two tiers comparable.
    import build_scores
    pool = sorted(set(build_scores.panel(db)) | set(tickers))
    scored = score_universe(db, as_of=as_of, tickers=pool)
    pct = {t: {k: r.percentile for k, r in ranks.items() if r.percentile is not None}
           for t, ranks in scored.ranked.items()}

    prov = Provenance(as_of=as_of, tickers=list(tickers))
    header = (f"All information is as at the close on {as_of}. "
              f"Nothing dated after it exists yet.\n")
    blocks: dict[str, list[str]] = {a: [header] for a in arms}

    # The placebo permutation: each covered company's findings are handed to a
    # different covered company. Fixed by the date so it is reproducible, and
    # derangement-checked so no company keeps its own.
    order = [t for t in tickers if by_company.get(companies[t].id)]
    shifted = order[1:] + order[:1] if len(order) > 1 else order
    placebo_source = dict(zip(order, shifted))

    for t in tickers:
        co = companies[t]
        hist = load_history(db, co.id, since=date(1990, 1, 1))
        last = hist.on_or_before(as_of) if hist else None
        if last is None:
            continue
        # The bar actually quoted, not the newest stored. on_or_before is
        # strictly backwards, so this is the number the reader sees and the
        # only one whose date can leak into the packet.
        prov.newest_bar = max(prov.newest_bar or last.session_date, last.session_date)

        st = standing(hist, as_of)
        head = (f"## {t} — {co.name}  (last close {float(last.close):.2f})\n"
                f"Sector: {co.sector or 'unclassified'}")
        price_line = st.summary if st else "No price history."
        sigs = by_company.get(co.id, [])
        for s in sigs:
            d = s.occurred_at.date()
            prov.newest_signal = max(prov.newest_signal or d, d)
            prov.signal_ids.append(str(s.id))

        p = pct.get(t, {})
        brief = build_brief(sigs, now=datetime.combine(as_of, datetime.min.time(),
                                                      timezone.utc), norms=norms)
        cons = find_contradictions(sigs, p, stance=brief.stance.value)
        prov.verdicts[t] = brief.stance.value

        def evidence_body(source_sigs: list[Signal], *, want: set[str]) -> list[str]:
            """The evidence block, assembled from named parts so an ablation is
            the removal of a part rather than a second code path."""
            b = [head]
            if source_sigs is sigs:
                dr, cp, cn = brief.drivers, brief.counterpoint, cons
            else:
                alt = build_brief(source_sigs, now=datetime.combine(
                    as_of, datetime.min.time(), timezone.utc), norms=norms)
                dr, cp, cn = alt.drivers, alt.counterpoint, find_contradictions(
                    source_sigs, p, stance=alt.stance.value)
            if "findings" in want and dr:
                b.append("WHAT LOOM FOUND:")
                for d_ in dr:
                    b.append(f"  [{d_.direction}] {d_.title}: {(d_.detail or '')[:180]}")
            if "findings" in want and cp:
                b.append(f"POINTING THE OTHER WAY:\n  {cp.title}: {(cp.detail or '')[:180]}")
            if "contradictions" in want and cn:
                b.append("WHERE LOOM'S SOURCES DISAGREE:")
                for x in cn:
                    b.append(f"  {x.headline}\n    {x.plain[:240]}")
            if "factors" in want:
                f = _fmt_factors(p)
                if f:
                    b.append("RANKED AGAINST COMPARABLE COMPANIES (0 worst, 1 best):")
                    b.extend(f)
            if "price" in want:
                b.append(f"PRICE CONTEXT: {price_line}")
            return b

        full_parts = {"findings", "contradictions", "factors", "price"}
        verdict_line = f"LOOM VERDICT: {brief.stance.value} — {brief.headline}"

        for arm in arms:
            if arm == "control":
                blocks[arm].append(f"{head}\n{price_line}")
            elif arm == "evidence":
                blocks[arm].append("\n".join(evidence_body(sigs, want=full_parts)))
            elif arm == "full":
                b = evidence_body(sigs, want=full_parts)
                blocks[arm].append("\n".join([b[0], verdict_line] + b[1:]))
            elif arm == "verdict_only":
                blocks[arm].append(f"{head}\n{verdict_line}\n{price_line}")
            elif arm == "placebo":
                src = placebo_source.get(t)
                donor = by_company.get(companies[src].id, []) if src else []
                blocks[arm].append("\n".join(evidence_body(donor, want=full_parts)))
            elif arm in spec.ABLATIONS:
                blocks[arm].append("\n".join(evidence_body(
                    sigs, want=full_parts - {spec.ABLATIONS[arm]})))
            elif arm == "degraded":
                weak = _degrade(sigs)
                blocks[arm].append("\n".join(evidence_body(weak, want=full_parts)))
            elif arm == "quant_loom":
                b = [head]
                f = _fmt_factors(p)
                if f:
                    b.append("RANKED AGAINST COMPARABLE COMPANIES (0 worst, 1 best):")
                    b.extend(f)
                b.append(f"PRICE CONTEXT: {price_line}")
                blocks[arm].append("\n".join(b))

    row = db.execute(sqltext("""
        select max(f.as_of_date) d from structured_facts f
        join companies c on c.id = f.company_id
        where c.ticker = any(:ts) and f.as_of_date <= :cut
    """), {"ts": list(tickers), "cut": as_of}).first()
    prov.newest_fact_filed = row.d if row else None

    packets = {a: "\n\n---\n\n".join(v) for a, v in blocks.items()}
    prov.audit(db)
    text_audit(packets, as_of)
    return packets, prov


def fingerprint(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()[:16]
