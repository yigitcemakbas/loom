"""Threshold rules over structured facts. The first signals with no model call.

Everywhere else in the engine, a language model is doing work that genuinely
needs language judgement. Nothing here does. "Three officers sold on the open
market inside ten days" is arithmetic, and asking a model to reach that
conclusion would be slower, costlier, and less reliable than counting.

Two rules ship in Phase 5:

  insider-sell cluster    several *discretionary* insider sales close together
  short-interest spike    a reading well above the prior one

The word discretionary is doing the work in the first rule. Most Form 4
disposals are shares withheld to cover tax on vesting equity, or the disposal
half of an option exercise. Those are consequences of a compensation schedule,
not decisions, and a rule that counted them would fire on nearly every large
company every quarter, which is the same as never firing at all. Only codes S
and P survive the filter (see ingestion/facts/sec_form4.py).
"""

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from app.models.structured_fact import FactType, StructuredFact

logger = logging.getLogger(__name__)

# Window for treating separate trades as one episode. Long enough to catch a
# staggered exit, short enough that ordinary quarterly activity does not
# accumulate into a false cluster.
INSIDER_WINDOW_DAYS = 14

# Below three participants it is one person's liquidity decision, which says
# little about the company. Agreement across people is the signal.
MIN_INSIDERS_IN_CLUSTER = 3

# Ignore trivia: a token sale should not count toward a cluster.
MIN_TRADE_VALUE_USD = 50_000

# Relative jump that reads as a real change in positioning rather than noise.
SHORT_INTEREST_SPIKE_RATIO = 1.20


@dataclass
class FactFinding:
    """A rule that fired. Deliberately not a Signal: the writer builds those,
    so these stay testable without touching the ORM or a session."""

    rule: str
    summary: str
    detail: str
    direction: str          # positive | negative | neutral
    magnitude: str          # minor | moderate | major
    horizon: str            # near_term | multi_quarter | structural
    confidence: float
    occurred_at: date
    evidence: dict


def _usd(fact: StructuredFact) -> float:
    value = (fact.attributes or {}).get("value_usd")
    return float(value) if value else 0.0


def insider_sell_cluster(facts: list[StructuredFact]) -> FactFinding | None:
    """Fire when several insiders made discretionary sales in one window.

    Returns the most recent qualifying cluster, or None. Buys are handled by
    the sibling rule below, because an insider *buying* is a much rarer and
    stronger signal and should never be averaged in with selling.
    """
    sales = [
        f for f in facts
        if f.fact_type == FactType.INSIDER_TRANSACTION
        and (f.attributes or {}).get("is_open_market")
        and (f.attributes or {}).get("disposed")
        and _usd(f) >= MIN_TRADE_VALUE_USD
    ]
    return _cluster(sales, selling=True)


def insider_buy_cluster(facts: list[StructuredFact]) -> FactFinding | None:
    """Fire when several insiders bought on the open market in one window.

    Held to the same participant threshold but reported as a stronger signal:
    insiders sell for many reasons (diversification, tax, a house) and buy for
    essentially one.
    """
    buys = [
        f for f in facts
        if f.fact_type == FactType.INSIDER_TRANSACTION
        and (f.attributes or {}).get("is_open_market")
        and not (f.attributes or {}).get("disposed")
        and _usd(f) >= MIN_TRADE_VALUE_USD
    ]
    return _cluster(buys, selling=False)


def _cluster(trades: list[StructuredFact], *, selling: bool) -> FactFinding | None:
    if len(trades) < MIN_INSIDERS_IN_CLUSTER:
        return None

    ordered = sorted(trades, key=lambda f: f.as_of_date, reverse=True)
    for anchor_index, anchor in enumerate(ordered):
        window_start = anchor.as_of_date - timedelta(days=INSIDER_WINDOW_DAYS)
        in_window = [
            f for f in ordered[anchor_index:] if f.as_of_date >= window_start
        ]
        people = {(f.attributes or {}).get("owner") for f in in_window}
        people.discard(None)
        if len(people) < MIN_INSIDERS_IN_CLUSTER:
            continue

        total_usd = sum(_usd(f) for f in in_window)
        total_shares = sum(abs(float(f.value or 0)) for f in in_window)
        verb = "sold" if selling else "bought"

        return FactFinding(
            rule="insider_sell_cluster" if selling else "insider_buy_cluster",
            summary=(
                f"{len(people)} insiders {verb} on the open market within "
                f"{INSIDER_WINDOW_DAYS} days"
            ),
            detail=(
                f"{len(in_window)} discretionary transactions totalling "
                f"{total_shares:,.0f} shares (about ${total_usd:,.0f}). "
                f"Routine vesting and option-exercise activity is excluded, so "
                f"these are decisions to trade rather than scheduled events."
            ),
            direction="negative" if selling else "positive",
            # Never "major": insider activity is corroborating evidence, not a
            # thesis on its own, and overstating it is how this data gets misread.
            magnitude="moderate" if len(people) > MIN_INSIDERS_IN_CLUSTER else "minor",
            horizon="near_term",
            # Deterministic, so confidence reflects the rule's own reliability
            # rather than any uncertainty about what the numbers say.
            confidence=0.8,
            occurred_at=anchor.as_of_date,
            evidence={
                "insiders": sorted(p for p in people if p),
                "transactions": len(in_window),
                "total_shares": total_shares,
                "total_usd": total_usd,
                "window_days": INSIDER_WINDOW_DAYS,
            },
        )
    return None


def short_interest_spike(facts: list[StructuredFact]) -> FactFinding | None:
    """Fire when the latest short-interest reading jumps against the prior one.

    Needs two readings; a single number says nothing about direction. FINRA
    publishes twice a month, so "prior" means the previous settlement date
    rather than an arbitrary lookback.
    """
    readings = sorted(
        [f for f in facts if f.fact_type == FactType.SHORT_INTEREST and f.value is not None],
        key=lambda f: f.as_of_date,
    )
    if len(readings) < 2:
        return None

    previous, latest = readings[-2], readings[-1]
    before, after = float(previous.value), float(latest.value)
    if before <= 0 or after < before * SHORT_INTEREST_SPIKE_RATIO:
        return None

    increase = (after / before - 1) * 100
    return FactFinding(
        rule="short_interest_spike",
        summary=f"Short interest rose {increase:.0f}% versus the prior reading",
        detail=(
            f"{before:,.0f} to {after:,.0f} {latest.unit or 'shares'} between "
            f"{previous.as_of_date} and {latest.as_of_date}. A rise means more "
            f"capital positioned against the stock, not that those positions are right."
        ),
        direction="negative",
        magnitude="moderate" if increase >= 50 else "minor",
        horizon="near_term",
        confidence=0.8,
        occurred_at=latest.as_of_date,
        evidence={
            "previous": before,
            "latest": after,
            "increase_percent": round(increase, 1),
            "previous_date": previous.as_of_date.isoformat(),
        },
    )


ALL_RULES = (insider_sell_cluster, insider_buy_cluster, short_interest_spike)


def evaluate(facts: list[StructuredFact]) -> list[FactFinding]:
    """Run every rule over one company's facts. Rules are independent, so one
    raising must not silence the others."""
    findings: list[FactFinding] = []
    for rule in ALL_RULES:
        try:
            finding = rule(facts)
        except Exception:
            logger.exception("Fact rule %s failed", getattr(rule, "__name__", rule))
            continue
        if finding is not None:
            findings.append(finding)
    return findings
