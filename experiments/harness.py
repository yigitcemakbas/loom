"""Test scaffolding for the paired-control trials. Reads Loom, never writes to it.

Everything here sits outside the application: it imports Loom's engine to build
point-in-time views and reads its database, and it changes nothing. The one
thing that might look like a product change is the evidence-only arm, which is
a filter applied when writing a packet. Loom still computes the stance; the
packet simply omits it, which is the whole point of that arm.

Three arms, and the middle one is why this exists:

  control    market data only
  evidence   Loom's findings, factors, contradictions and price context,
             with the verdict removed
  full       the same, plus Loom's verdict

Comparing evidence against full isolates whether any effect comes from the
research or from the synthesised conclusion. That matters because the verdict
is the component that has failed five separate measurement approaches and is
nonetheless the thing readers were observed leaning on.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from math import sqrt
from statistics import pstdev
from typing import Iterable, Optional

from sqlalchemy import func, select

from app.db.session import SessionLocal
from app.engine.brief import build_brief
from app.engine.contradiction import find_contradictions
from app.engine.disclosure import measure_norms
from app.engine.price_context import standing
from app.engine.price_loader import load_benchmark, load_history
from app.engine.quant.crosssection import percentile_phrase
from app.engine.quant.factors import FACTORS_BY_KEY
from app.engine.quant.runner import score_universe
from app.models.company import Company
from app.models.signal import Signal

ARMS = ("control", "evidence", "full")

# Findings a company needs by the decision date before it is worth including.
# Below this Loom has effectively nothing to say and the arm collapses to the
# control, which wastes a cell rather than testing anything.
MIN_FINDINGS = 5

# Sessions in a year, for annualising. Stated rather than derived because the
# calendar varies and nothing here turns on the third decimal.
SESSIONS_PER_YEAR = 252


# --------------------------------------------------------------- universe


@dataclass(frozen=True)
class Period:
    """One decision date and the window it is scored over."""

    start: date
    end: date

    @property
    def label(self) -> str:
        return f"{self.start.isoformat()}_{self.end.isoformat()}"


def pick_universe(db, as_of: date, size: int = 25) -> list[str]:
    """The companies Loom could actually say something about on that date.

    Ordered by how much Loom had read of them, then by size, so the universe
    is the best-covered names rather than an arbitrary slice. Deterministic:
    the same date and size always give the same list, which is what lets a
    rerun be a rerun.
    """
    cutoff = datetime.combine(as_of, datetime.max.time(), timezone.utc)
    rows = db.execute(
        select(Company.ticker, func.count(Signal.id).label("n"), Company.sec_rank)
        .join(Signal, Signal.company_id == Company.id)
        .where(Signal.occurred_at <= cutoff)
        .group_by(Company.ticker, Company.sec_rank)
        .having(func.count(Signal.id) >= MIN_FINDINGS)
    ).all()
    ranked = sorted(
        rows,
        key=lambda r: (-r[1], r[2] if r[2] is not None else 10**9, r[0]),
    )
    return [r[0] for r in ranked[:size]]


# ----------------------------------------------------------------- packets


def _fmt_factors(pct: dict, limit: int = 8) -> list[str]:
    ranked = sorted(
        [(k, v) for k, v in pct.items() if v is not None and k != "composite"],
        key=lambda kv: abs(kv[1] - 0.5), reverse=True,
    )[:limit]
    out = []
    for k, v in ranked:
        f = FACTORS_BY_KEY.get(k)
        out.append(f"  {(f.label if f else k):32s} {v:.2f}  {percentile_phrase(v)}")
    return out


def build_packets(db, period: Period, tickers: list[str]) -> dict[str, str]:
    """One text packet per arm, containing nothing dated after the decision date.

    The no-lookahead rule is enforced at the source rather than by review:
    findings are filtered by `occurred_at`, factor percentiles are recomputed
    with the point-in-time scorer instead of read from the stored table, and
    price context is taken as at the decision date. A packet is audited after
    it is written and the build fails rather than ships if anything slips.
    """
    as_of = period.start
    cutoff = datetime.combine(as_of, datetime.max.time(), timezone.utc)
    companies = {c.ticker: c for c in db.execute(select(Company)).scalars()}

    past = list(db.execute(select(Signal).where(Signal.occurred_at <= cutoff)).scalars())
    sectors = {str(c.id): c.sector for c in companies.values() if c.sector}
    norms = measure_norms(past, sectors)
    by_company: dict[object, list] = {}
    for s in past:
        by_company.setdefault(s.company_id, []).append(s)

    scored = score_universe(db, as_of=as_of)
    pct = {
        t: {k: r.percentile for k, r in ranks.items() if r.percentile is not None}
        for t, ranks in scored.ranked.items()
    }

    header = (
        f"All information is as at the close on {as_of}. "
        f"Nothing dated after it is available to you.\n"
    )
    blocks: dict[str, list[str]] = {a: [header] for a in ARMS}

    for t in tickers:
        co = companies[t]
        hist = load_history(db, co.id, since=date(1990, 1, 1))
        last = hist.on_or_before(as_of)
        if last is None:
            continue
        st = standing(hist, as_of)
        head = (f"## {t} — {co.name}  (last close {float(last.close):.2f})\n"
                f"Sector: {co.sector}")
        price_line = st.summary if st else "No price history."

        blocks["control"].append(f"{head}\n{price_line}")

        sigs = by_company.get(co.id, [])
        brief = build_brief(
            sigs, now=datetime.combine(as_of, datetime.min.time(), timezone.utc), norms=norms
        )
        p = pct.get(t, {})
        cons = find_contradictions(sigs, p, stance=brief.stance.value)

        body = [head]
        # The verdict, and only the verdict, separates the two Loom arms.
        body_full = [f"LOOM VERDICT: {brief.stance.value} — {brief.headline}"]

        if brief.drivers:
            body.append("WHAT LOOM FOUND:")
            for dr in brief.drivers:
                body.append(f"  [{dr.direction}] {dr.title}: {(dr.detail or '')[:180]}")
        if brief.counterpoint:
            cp = brief.counterpoint
            body.append(f"POINTING THE OTHER WAY:\n  {cp.title}: {(cp.detail or '')[:180]}")
        if cons:
            body.append("WHERE LOOM'S SOURCES DISAGREE:")
            for x in cons:
                body.append(f"  {x.headline}\n    {x.plain[:240]}")
        factors = _fmt_factors(p)
        if factors:
            body.append("RANKED AGAINST COMPARABLE COMPANIES (0 worst, 1 best):")
            body.extend(factors)
        body.append(f"PRICE CONTEXT: {price_line}")

        blocks["evidence"].append("\n".join(body))
        blocks["full"].append("\n".join([body[0]] + body_full + body[1:]))

    packets = {a: "\n\n---\n\n".join(v) for a, v in blocks.items()}
    _audit(packets, as_of)
    return packets


def _audit(packets: dict[str, str], as_of: date) -> None:
    """Refuse to ship a packet containing anything dated after the decision.

    Checked here rather than by eye. A leak is invisible in the output and
    fatal to the result, so it has to fail the build.
    """
    import re

    pattern = re.compile(r"\b(20\d{2})-(\d{2})-(\d{2})\b")
    for arm, text in packets.items():
        for m in pattern.finditer(text):
            found = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
            if found > as_of:
                raise AssertionError(
                    f"{arm} packet leaks {found}, after the decision date {as_of}"
                )
    # The verdict must appear in exactly one arm.
    if "LOOM VERDICT" in packets["evidence"]:
        raise AssertionError("evidence arm contains the verdict it exists to exclude")
    if "LOOM VERDICT" not in packets["full"]:
        raise AssertionError("full arm is missing the verdict")


# ----------------------------------------------------------------- metrics


@dataclass
class Book:
    """One agent's allocation, and everything measurable about how it did."""

    arm: str
    persona: str
    seed: int
    period: str
    positions: dict[str, tuple[str, float, float]]  # ticker -> (action, weight, conviction)

    # filled by measure()
    total_return: float = 0.0
    deployed: float = 0.0
    cash: float = 0.0
    volatility: float = 0.0
    max_drawdown: float = 0.0
    downside_deviation: float = 0.0
    sharpe: Optional[float] = None
    sortino: Optional[float] = None
    hhi: float = 0.0
    n_shorts: int = 0
    short_weight: float = 0.0
    hit_rate: Optional[float] = None
    avg_position_return: Optional[float] = None
    daily: list[float] = field(default_factory=list)


def _signed(action: str, weight: float) -> float:
    """Exposure, signed. A pass is zero rather than absent, because a decision
    not to hold is a decision."""
    if action == "buy":
        return weight
    if action in ("short", "sell"):
        return -weight
    return 0.0


def measure(book: Book, daily_returns: dict[str, dict[date, float]],
            window: list[date]) -> Book:
    """Fill in every metric from the position set and the price series.

    The portfolio's daily series is rebuilt rather than approximated from the
    endpoints, because volatility, drawdown and the downside measures are
    properties of the path and endpoints cannot see them. Cash earns nothing,
    which is the honest assumption over a quarter at these rates and keeps
    arms with different deployment comparable.
    """
    exposure = {t: _signed(a, w) for t, (a, w, _) in book.positions.items()}
    book.deployed = sum(abs(e) for e in exposure.values())
    book.cash = max(0.0, 1.0 - book.deployed)
    book.n_shorts = sum(1 for e in exposure.values() if e < 0)
    book.short_weight = sum(-e for e in exposure.values() if e < 0)
    # Concentration over gross exposure. A book of one name scores 1.0 and an
    # evenly spread book of n scores 1/n.
    gross = book.deployed or 1.0
    book.hhi = sum((abs(e) / gross) ** 2 for e in exposure.values())

    series = []
    for d in window:
        r = sum(e * daily_returns.get(t, {}).get(d, 0.0) for t, e in exposure.items())
        series.append(r)
    book.daily = series

    compounded = 1.0
    peak, trough = 1.0, 0.0
    for r in series:
        compounded *= 1 + r
        peak = max(peak, compounded)
        trough = min(trough, compounded / peak - 1)
    book.total_return = compounded - 1
    book.max_drawdown = trough

    if len(series) > 1:
        book.volatility = pstdev(series) * sqrt(SESSIONS_PER_YEAR)
        downside = [min(0.0, r) for r in series]
        book.downside_deviation = pstdev(downside) * sqrt(SESSIONS_PER_YEAR)
        mean_ann = (sum(series) / len(series)) * SESSIONS_PER_YEAR
        book.sharpe = mean_ann / book.volatility if book.volatility > 0 else None
        book.sortino = (
            mean_ann / book.downside_deviation if book.downside_deviation > 0 else None
        )

    held = [(t, e) for t, e in exposure.items() if e != 0]
    if held:
        per = []
        for t, e in held:
            compounded_t = 1.0
            for d in window:
                compounded_t *= 1 + daily_returns.get(t, {}).get(d, 0.0)
            per.append((compounded_t - 1) * (1 if e > 0 else -1))
        book.hit_rate = sum(1 for x in per if x > 0) / len(per)
        book.avg_position_return = sum(per) / len(per)
    return book


@dataclass
class PairedEffect:
    """One decision, compared against the same agent's control decision."""

    persona: str
    seed: int
    period: str
    ticker: str
    arm: str
    control_exposure: float
    arm_exposure: float
    stock_return: float
    conviction_control: float
    conviction_arm: float

    @property
    def delta(self) -> float:
        return self.arm_exposure - self.control_exposure

    @property
    def contribution(self) -> float:
        """What the change in exposure earned or cost, in return terms."""
        return self.delta * self.stock_return

    @property
    def kind(self) -> str:
        """Whether this was upside given away or loss avoided.

        The distinction the whole question turns on. Reducing exposure to a
        stock that rose is missed upside; reducing exposure to one that fell
        is avoided loss. A tool that reliably does the second and not the
        first is worth having even if its average effect is zero.
        """
        if abs(self.delta) < 1e-9:
            return "unchanged"
        if self.contribution < 0:
            return "missed_upside" if self.stock_return > 0 else "added_loss"
        return "avoided_loss" if self.stock_return < 0 else "captured_upside"


def paired_effects(books: list[Book], total_returns: dict[str, float]) -> list[PairedEffect]:
    """Every treated decision against its own control, matched on persona,
    seed, period and ticker so nothing but the arm differs."""
    control = {
        (b.persona, b.seed, b.period): b for b in books if b.arm == "control"
    }
    out = []
    for b in books:
        if b.arm == "control":
            continue
        base = control.get((b.persona, b.seed, b.period))
        if base is None:
            continue
        for t, (a, w, cv) in b.positions.items():
            ca, cw, ccv = base.positions.get(t, ("pass", 0.0, 0.0))
            out.append(PairedEffect(
                persona=b.persona, seed=b.seed, period=b.period, ticker=t, arm=b.arm,
                control_exposure=_signed(ca, cw), arm_exposure=_signed(a, w),
                stock_return=total_returns.get(t, 0.0),
                conviction_control=ccv, conviction_arm=cv,
            ))
    return out


def load_returns(db, tickers: list[str], period: Period):
    """Daily and total returns per ticker over the window, from stored bars."""
    daily, total, window = {}, {}, []
    for t in tickers:
        co = db.execute(select(Company).where(Company.ticker == t)).scalars().first()
        if co is None:
            continue
        h = load_history(db, co.id, since=date(1990, 1, 1))
        rr = h.returns_by_date(period.start, period.end)
        daily[t] = rr
        a, z = h.on_or_before(period.start), h.on_or_before(period.end)
        if a and z and float(a.adjusted_close) > 0:
            total[t] = float(z.adjusted_close) / float(a.adjusted_close) - 1.0
        window = sorted(set(window) | set(rr.keys()))
    return daily, total, window
