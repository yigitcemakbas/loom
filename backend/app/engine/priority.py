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
) -> float:
    """Return the feed ranking score for one signal."""
    confidence = min(max(confidence, 0.0), 1.0)
    weight = TYPE_WEIGHTS.get(signal_type, 0.5)
    return round(confidence * weight * recency_factor(occurred_at, now), 6)
