"""Ranking score for the signal feed. Deterministic, no LLM.

The feed is meant to be a short ranked list, not a firehose. Priority
combines three things:

  confidence     how sure the extraction was
  type weight    how trustworthy this *kind* of signal is
  recency        how much it still matters

Type weight encodes a real difference in evidence quality. A newly appeared
risk factor is established by comparing two filings, deterministic, checkable
against the source text. A sentiment read is a language judgement that can be
wrong in ways nobody can verify. The first should outrank the second even when
the model reports similar confidence in both.
"""

import math
from datetime import datetime, timezone

from app.models.signal import SignalType

# Higher = more trustworthy as evidence.
TYPE_WEIGHTS: dict[SignalType, float] = {
    SignalType.NEW_RISK_FACTOR: 1.0,   # verifiable against the prior filing
    SignalType.QOQ_ANOMALY: 1.0,       # verifiable against the prior filing
    # Top band with the diff-verified types, on the same reasoning: its
    # evidence base is several independently extracted, quote-checked findings
    # plus a deterministic gate, which is broader than any single one of them.
    # Not set above 1.0, that would be ranking it by how useful the synthesis
    # is rather than by how good its evidence is, and this table means the
    # latter. A pattern therefore sits alongside its constituents in the feed
    # rather than automatically on top of them.
    SignalType.EMERGING_PATTERN: 1.0,
    SignalType.GUIDANCE_CHANGE: 0.9,   # concrete, usually quoted verbatim
    # Derived by arithmetic from filed transactions, so the *fact* is beyond
    # dispute. Ranked below the filing-derived types anyway, because what an
    # insider's sale implies about the business is genuinely ambiguous: people
    # sell for diversification, tax, and houses, not only for outlook.
    SignalType.INSIDER_ACTIVITY: 0.85,
    # Equally verifiable, but a step further removed: it reports what other
    # traders believe about the company rather than anything the company did.
    SignalType.SHORT_INTEREST_SPIKE: 0.75,
    SignalType.SENTIMENT_SHIFT: 0.7,   # a judgement call
    SignalType.NOTABLE_QUOTE: 0.6,     # real, but interesting rather than conclusive
}

# Signals decay to ~37% weight at this age, so a stale finding never outranks
# a fresh one of similar quality.
_RECENCY_HALFLIFE_DAYS = 90.0

# How much the assessed materiality moves the ranking.
#
# This was missing, and its absence was measurable. The assessment step labels
# every finding minor, moderate or major, and 62 of the stored findings are
# major — but the score was confidence x type x recency, so a catastrophic
# disclosure and a boilerplate one ranked identically whenever the extraction
# happened to be equally sure of both. Loom knew which findings mattered and
# discarded it at the only point where it would have changed what a reader sees.
#
# Deliberately a modest spread rather than a large one. Materiality is the one
# component here that is a judgement about consequence rather than a fact about
# the document, so it adjusts the ranking without being allowed to dominate the
# evidence-quality weighting that the type table encodes.
MAGNITUDE_WEIGHTS = {
    "major": 1.35,
    "moderate": 1.0,
    "minor": 0.75,
}
_DEFAULT_MAGNITUDE = 1.0


def recency_factor(occurred_at: datetime, now: datetime | None = None) -> float:
    """Exponential decay on age, clamped to (0, 1]."""
    now = now or datetime.now(timezone.utc)
    if occurred_at.tzinfo is None:
        occurred_at = occurred_at.replace(tzinfo=timezone.utc)
    age_days = max((now - occurred_at).total_seconds() / 86400.0, 0.0)
    return math.exp(-age_days / _RECENCY_HALFLIFE_DAYS)


def score(
    signal_type: SignalType,
    confidence: float,
    occurred_at: datetime,
    now: datetime | None = None,
    magnitude: str | None = None,
) -> float:
    """Return the feed ranking score for one signal.

    `magnitude` is the assessed materiality and is optional: a caller that does
    not have one gets the same score it got before, so nothing that already
    ranks is silently reordered by an absent field.
    """
    confidence = min(max(confidence, 0.0), 1.0)
    weight = TYPE_WEIGHTS.get(signal_type, 0.5)
    material = MAGNITUDE_WEIGHTS.get((magnitude or "").lower(), _DEFAULT_MAGNITUDE)
    return round(
        confidence * weight * material * recency_factor(occurred_at, now), 6
    )
