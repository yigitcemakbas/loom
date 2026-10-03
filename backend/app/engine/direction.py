"""Which way a finding points, decided by what the document did.

This module exists to correct a semantic drift, and the drift is worth recording
because every measurement taken of this engine was really a measurement of it.

A finding's direction used to come from `market_direction`, which is the output
of `prompts/market_reaction.py` — a model's characterisation of "how markets
typically read this TYPE of disclosure", explicitly modelled on a research note
saying "we would expect shares to react negatively to this". That field is honest
about what it is. The problem was what it was used for: `sign_of` returned nothing
else, so the stance, the drivers, the counterpoint and the sufficiency gate were
all computed from it. Loom's verdict was therefore a genre-normalised average of
a language model's expected market reactions.

That is a price forecast. The founding constraint says Loom is not one, and
`market_context.py` already records an earlier version of this same confusion:
refusing to forecast a price is different from refusing to look at one. The
direction pipeline crossed the line that module drew — it forecast no number, but
it forecast a sign, and the sign was the product.

Measured consequences, all of which this is meant to remove: the verdict agreed
with Loom's own later findings on 55.6% of cases, a coin flip; it correlated with
forward price at rho about zero; and reader agents handed it lost 10.1 points
against reading the same evidence without it, p=0.005, by shorting into two
+22% quarters.

So direction is derived here from documentary facts only — what the filing did,
not what a market might do about it. Where the document has no sign, this returns
None, and None is not zero: an unassessed finding is not a balanced one.
"""

from __future__ import annotations

from typing import Optional

from app.models.signal import SignalType

# A risk factor that appeared is a fact about the document: the risk section
# acquired a disclosure it did not previously carry. A quote is not a fact about
# direction at all, whatever it says, which is why it abstains rather than votes.
_SENTIMENT_TYPES = frozenset({SignalType.SENTIMENT_SHIFT, SignalType.EMERGING_PATTERN})

# Comparisons that are themselves a risk diff, and therefore carry the same
# documentary sign as a new risk factor. These arrive labelled QOQ_ANOMALY for
# historical reasons; the `comparison` field is what actually says what was done.
_RISK_DIFF_COMPARISONS = frozenset({"year_over_year_risk_factors"})

# Loom has no signal for a risk factor *disappearing*, because `find_changed_
# paragraphs` only ever iterates the current filing looking for paragraphs absent
# from the prior one. It never looks the other way. So the engine can see risk
# arriving and never risk resolving, which biases every stance negative by
# construction. Adding the type needs a database migration and a bidirectional
# diff, so it is looked up by name here: this module supports it the moment it
# exists and does not break while it does not.
_RESOLVED = getattr(SignalType, "RESOLVED_RISK_FACTOR", None)


def documentary_sign(signal) -> Optional[float]:
    """+1, -1, or None. Never 0.0 for "we could not tell".

    None means the document did not state a direction, which is different from
    stating a balanced one. Callers that want to treat silence as neutral must
    do it knowingly.
    """
    kind = getattr(signal, "signal_type", None)
    meta = getattr(signal, "signal_metadata", None) or {}

    # The risk section grew. Documentary, and checkable against the two filings
    # the diff was computed from.
    if kind == SignalType.NEW_RISK_FACTOR:
        return -1.0
    if _RESOLVED is not None and kind == _RESOLVED:
        return 1.0

    if kind == SignalType.QOQ_ANOMALY:
        if str(meta.get("comparison") or "") in _RISK_DIFF_COMPARISONS:
            return -1.0
        # A quarter-over-quarter change in management's discussion is a real
        # event, but which way it points depends on the metric that moved, and
        # the metric is not stored. Abstain rather than guess.
        return None

    if kind == SignalType.INSIDER_ACTIVITY:
        rule = str(meta.get("rule") or "").lower()
        if "sell" in rule:
            return -1.0
        if "buy" in rule:
            return 1.0
        return None

    # Short interest is a measured position, not a disclosure, but it is a fact
    # rather than a forecast: more shares have been sold short.
    if kind == SignalType.SHORT_INTEREST_SPIKE:
        return -1.0

    # A shift in the tone of the company's own language is a property of the
    # text. This is the one place a language judgement is the documentary fact.
    if kind in _SENTIMENT_TYPES:
        score = getattr(signal, "sentiment_score", None)
        if score is None:
            return None
        if score > 0:
            return 1.0
        if score < 0:
            return -1.0
        return None

    if kind == SignalType.GUIDANCE_CHANGE:
        return _guidance_sign(meta)

    # A quote is evidence, not a vote. However strongly a passage reads, the
    # company did not take a direction by being quotable.
    return None


# Metrics whose movement has a settled meaning, and the metrics that do not.
#
# The split is the whole content of the guidance rule. Revenue guidance going up
# is good news in a way that needs no interpretation; cost guidance going up is
# bad news on the same terms. Capital expenditure is neither: a company raising
# capex may be investing into demand or bleeding into maintenance, and the
# guidance sentence alone does not say which. Spend is listed as ambiguous for
# that reason rather than being forced into one bucket to raise coverage.
_HIGHER_IS_BETTER = (
    "revenue", "sales", "earnings", "eps", "earnings per share", "income",
    "profit", "margin", "cash flow", "fcf", "ebitda", "bookings", "backlog",
    "orders", "subscribers", "users", "units", "shipments", "yield",
    "utilisation", "utilization", "same-store", "comparable sales",
)
_HIGHER_IS_WORSE = (
    "cost", "costs", "expense", "expenses", "opex", "sg&a", "churn",
    "attrition", "tax rate", "leverage", "debt", "dilution", "interest expense",
    "impairment", "provision", "loss", "deficit", "warranty",
)
# Named so the abstention is deliberate and visible rather than a fallthrough.
_POLARITY_UNCLEAR = (
    "capital expenditure", "capex", "capital investment", "headcount",
    "employees", "inventory", "research and development", "r&d",
    "buyback", "repurchase", "dividend", "spend", "investment",
)


def _metric_polarity(metric: str) -> Optional[float]:
    """+1 where a higher figure is better, -1 where worse, None where neither.

    Ambiguity is checked first. "capital expenditure" contains no token from the
    other two lists today, but a future addition such as "investment income"
    would collide, and an ambiguous metric silently acquiring a polarity is the
    failure worth guarding against rather than the reverse.
    """
    if not metric:
        return None
    text = metric.strip().lower()
    if any(term in text for term in _POLARITY_UNCLEAR):
        return None
    if any(term in text for term in _HIGHER_IS_WORSE):
        return -1.0
    if any(term in text for term in _HIGHER_IS_BETTER):
        return 1.0
    return None


def _guidance_sign(meta: dict) -> Optional[float]:
    """The direction a guidance change states, from the figure and the move.

    Withdrawal is signed without consulting the metric. Removing a forecast the
    company previously committed to is a negative act about any figure: it
    narrows what a reader knows, and companies do not generally suspend guidance
    on good news. Reaffirming and initiating are genuinely directionless, and
    `None` says so rather than pretending to neutrality.
    """
    movement = str(meta.get("movement") or "").strip().lower()
    if movement == "withdrawn":
        return -1.0
    if movement not in ("raised", "lowered"):
        return None

    polarity = _metric_polarity(str(meta.get("metric") or ""))
    if polarity is None:
        return None
    return polarity if movement == "raised" else -polarity


# Types whose direction a document can state at all. A quote is not a
# direction-bearing finding however strongly it reads.
#
# Separating "cannot have a direction" from "has not been given one" is what
# keeps the sufficiency gate honest: the first is normal and the second is a
# gap, and counting quotes as gaps would refuse a verdict on every company whose
# record contains quotes.
#
# Guidance belongs here now that extraction records which figure moved. It is
# listed on what the document could state, not on what Loom managed to read, so
# a guidance change left unassessed counts against sufficiency rather than
# excusing itself. Findings written before the metric was captured are
# therefore unassessed gaps, which is the accurate description of them.
_DIRECTION_BEARING = frozenset(
    t for t in (
        getattr(SignalType, name, None) for name in (
            "NEW_RISK_FACTOR", "RESOLVED_RISK_FACTOR", "QOQ_ANOMALY",
            "INSIDER_ACTIVITY", "SHORT_INTEREST_SPIKE", "SENTIMENT_SHIFT",
            "EMERGING_PATTERN", "GUIDANCE_CHANGE",
        )
    ) if t is not None
)


def can_be_directional(signal) -> bool:
    """Whether a document could have stated a direction for this finding."""
    return getattr(signal, "signal_type", None) in _DIRECTION_BEARING


def label(signal) -> str:
    """The direction as a word, for display and for grouping."""
    sign = documentary_sign(signal)
    if sign is None:
        return "unassessed"
    return "positive" if sign > 0 else "negative"


def is_directional(signal) -> bool:
    return documentary_sign(signal) is not None


__all__ = ["documentary_sign", "label", "is_directional", "can_be_directional"]
