"""Short-window comparison: finding the clusters worth synthesising.

The engine's other comparison, the year-over-year risk diff, answers "what
changed since last year." It cannot see the case this module exists for: a
company files two 8-Ks and a news item inside one week that, read together,
change the picture in a way no single one of them does. Before this, that
produced three isolated signals and left the reader to join them up.

Everything here is deterministic and cheap. It decides **whether** a cluster is
worth a model call; it never decides what the cluster means. That split is the
same one the risk diff uses, and it is what keeps cost proportional to real
signal rather than to document volume: on a quiet week the gate simply never
fires and nothing is spent.

The gate deliberately requires two or more distinct source documents. A single
filing that produced five findings is not an emerging pattern, it is one
document, and `analyze_document` already covered it.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.models.signal import Signal, SignalType

logger = logging.getLogger(__name__)

# How far back a "short window" reaches. Seven days catches the same-day and
# same-week compression this module is for, without stretching far enough to
# start calling an ordinary reporting month a pattern.
DEFAULT_WINDOW_DAYS = 7

# Findings older than the window but recent enough to establish what "normal"
# looked like just before it, used only for the tone-reversal rule. Public
# because callers must load at least this much history for the rule to work.
BASELINE_DAYS = 90

# Rule thresholds. Each is one way a cluster earns a model call.
_MIN_DOCUMENTS = 2
_DRUMBEAT_MIN_ALIGNED = 3      # findings agreeing on a non-neutral direction
_SENTIMENT_REVERSAL_DELTA = 0.5  # swing vs baseline that reads as a real turn

# Signal types that describe a company's own disclosure. A prior emerging
# pattern is excluded so patterns never compound on themselves.
_CLUSTERABLE_TYPES = {
    SignalType.NEW_RISK_FACTOR,
    SignalType.NOTABLE_QUOTE,
    SignalType.GUIDANCE_CHANGE,
    SignalType.QOQ_ANOMALY,
    SignalType.SENTIMENT_SHIFT,
}


@dataclass
class Cluster:
    """A short window's worth of findings, plus why it cleared the gate."""

    signals: list[Signal]
    document_ids: list = field(default_factory=list)
    window_days: int = DEFAULT_WINDOW_DAYS
    triggers: list[str] = field(default_factory=list)
    dominant_direction: str | None = None
    sentiment_delta: float | None = None

    @property
    def anchor_signal(self) -> Signal:
        """Newest finding in the cluster. Its document anchors the signal."""
        return max(self.signals, key=lambda s: s.occurred_at)

    @property
    def oldest_signal(self) -> Signal:
        return min(self.signals, key=lambda s: s.occurred_at)

    @property
    def quotes(self) -> list[str]:
        return [s.evidence_quote for s in self.signals if s.evidence_quote]

    def as_findings(self) -> list[dict]:
        """Prompt-ready view. Ordered oldest first so the model reads the
        sequence the way it happened."""
        return [
            {
                "occurred_at": signal.occurred_at.date().isoformat(),
                "doc_subtype": (signal.signal_metadata or {}).get("doc_subtype")
                or signal.signal_type.value,
                "summary": signal.summary,
                "quote": signal.evidence_quote,
                "market_direction": signal.market_direction,
            }
            for signal in sorted(self.signals, key=lambda s: s.occurred_at)
        ]


def _tz_aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _dominant_direction(signals: list[Signal]) -> tuple[str | None, int]:
    """Most common non-neutral direction and how many findings share it."""
    counts: dict[str, int] = {}
    for signal in signals:
        if signal.market_direction in ("positive", "negative"):
            counts[signal.market_direction] = counts.get(signal.market_direction, 0) + 1
    if not counts:
        return None, 0
    direction = max(counts, key=lambda key: counts[key])
    return direction, counts[direction]


def _mean_sentiment(signals: list[Signal]) -> float | None:
    scores = [s.sentiment_score for s in signals if s.sentiment_score is not None]
    return sum(scores) / len(scores) if scores else None


def build_cluster(
    signals: list[Signal],
    *,
    window_days: int = DEFAULT_WINDOW_DAYS,
) -> Cluster | None:
    """Return a gated cluster for the window, or None if nothing qualifies.

    `signals` is the company's recent signals; the windowing happens here so
    callers do not each reimplement the cutoff.

    The window is anchored on the **most recent disclosure**, not on the clock.
    Anchoring on "now" would mean a genuine three-day burst became invisible
    the following week, which is exactly the case this module exists to catch.
    A burst stays a burst; how much it still matters is priority.py's job, and
    its recency decay already handles that.
    """
    candidates = [s for s in signals if s.signal_type in _CLUSTERABLE_TYPES]
    if not candidates:
        return None

    anchor_time = max(_tz_aware(s.occurred_at) for s in candidates)
    window_start = anchor_time - timedelta(days=window_days)
    baseline_start = window_start - timedelta(days=BASELINE_DAYS)

    in_window = [s for s in candidates if _tz_aware(s.occurred_at) >= window_start]
    baseline = [
        s for s in candidates
        if baseline_start <= _tz_aware(s.occurred_at) < window_start
    ]

    document_ids = list({s.source_document_id for s in in_window if s.source_document_id})
    if len(document_ids) < _MIN_DOCUMENTS:
        return None

    triggers: list[str] = []

    direction, aligned = _dominant_direction(in_window)
    if aligned >= _DRUMBEAT_MIN_ALIGNED:
        triggers.append(f"{aligned} findings aligned {direction}")

    # One major finding on its own is already covered by its own signal; it is
    # the corroboration from a second document that makes it a pattern.
    major = [s for s in in_window if s.market_magnitude == "major"]
    if major and len(in_window) >= 2:
        triggers.append("major finding corroborated by a second disclosure")

    window_sentiment = _mean_sentiment(in_window)
    baseline_sentiment = _mean_sentiment(baseline)
    sentiment_delta = None
    if window_sentiment is not None and baseline_sentiment is not None:
        sentiment_delta = window_sentiment - baseline_sentiment
        if abs(sentiment_delta) >= _SENTIMENT_REVERSAL_DELTA:
            triggers.append(f"tone swing of {sentiment_delta:+.2f} vs the prior 90 days")

    if not triggers:
        return None

    return Cluster(
        signals=in_window,
        document_ids=document_ids,
        window_days=window_days,
        triggers=triggers,
        dominant_direction=direction,
        sentiment_delta=sentiment_delta,
    )


def verify_anchor_quote(anchor_quote: str | None, supplied: list[str]) -> str | None:
    """Confirm the model returned one of the quotes it was given.

    The synthesis prompt is the one place a model is handed existing quotes and
    asked to choose among them, which is exactly where a paraphrase could slip
    into the evidence chain unnoticed. A quote that is not an exact match is
    dropped rather than shown: a pattern signal with no receipt is still
    useful, one with a fabricated receipt is not.
    """
    if not anchor_quote:
        return None
    if anchor_quote in supplied:
        return anchor_quote
    logger.warning("Emerging pattern: anchor quote did not match any supplied quote; dropping it.")
    return None
