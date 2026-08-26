"""Short-window clustering tests: the gate, not the synthesis.

Everything here is deterministic, so it is testable without a model. What is
being pinned down is when a cluster earns an LLM call and when it must not,
since that gate is the whole cost-control story for the feature.
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.engine.clustering import build_cluster, verify_anchor_quote
from app.models.signal import Signal, SignalType

NOW = datetime(2026, 8, 25, tzinfo=timezone.utc)
DOC_A = uuid.uuid4()
DOC_B = uuid.uuid4()


def _signal(
    *,
    days_ago: float,
    document_id=DOC_A,
    signal_type=SignalType.NEW_RISK_FACTOR,
    direction: str | None = "negative",
    magnitude: str | None = "moderate",
    sentiment: float | None = None,
    quote: str | None = None,
) -> Signal:
    return Signal(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        signal_type=signal_type,
        summary="finding",
        confidence=0.9,
        priority=0.5,
        occurred_at=NOW - timedelta(days=days_ago),
        source_document_id=document_id,
        market_direction=direction,
        market_magnitude=magnitude,
        sentiment_score=sentiment,
        evidence_quote=quote,
        signal_metadata={},
    )


def test_single_document_never_clusters():
    """One filing producing many findings is not a pattern, analyze_document
    already covered it. Requiring two documents is what makes this feature
    about cross-document synthesis rather than a second summary."""
    signals = [_signal(days_ago=1) for _ in range(5)]
    assert build_cluster(signals) is None


def test_two_documents_with_aligned_findings_cluster():
    signals = [
        _signal(days_ago=1, document_id=DOC_A),
        _signal(days_ago=2, document_id=DOC_B),
        _signal(days_ago=3, document_id=DOC_B),
    ]
    cluster = build_cluster(signals)
    assert cluster is not None
    assert cluster.dominant_direction == "negative"
    assert len(cluster.document_ids) == 2
    assert cluster.triggers


def test_a_burst_still_clusters_long_after_it_happened():
    """The window is anchored on the newest disclosure, not on the clock.
    Anchoring on 'now' would make a genuine three-day burst invisible the
    following week, which is the exact case this module exists to catch.
    How much an old pattern still matters is priority.py's decay, not this
    module's cutoff."""
    signals = [
        _signal(days_ago=120, document_id=DOC_A),
        _signal(days_ago=121, document_id=DOC_B),
        _signal(days_ago=122, document_id=DOC_B),
    ]
    cluster = build_cluster(signals)

    assert cluster is not None
    assert len(cluster.document_ids) == 2


def test_findings_outside_the_window_are_excluded():
    """Two documents months apart are ordinary reporting, not a short-window
    shift, that compression is the entire premise of the signal."""
    signals = [
        _signal(days_ago=1, document_id=DOC_A),
        _signal(days_ago=40, document_id=DOC_B),
        _signal(days_ago=41, document_id=DOC_B),
    ]
    assert build_cluster(signals) is None


def test_two_documents_without_a_trigger_do_not_cluster():
    """Two neutral, minor findings in a week is a normal week. Firing here
    would spend a model call to say nothing."""
    signals = [
        _signal(days_ago=1, document_id=DOC_A, direction="neutral", magnitude="minor"),
        _signal(days_ago=2, document_id=DOC_B, direction="neutral", magnitude="minor"),
    ]
    assert build_cluster(signals) is None


def test_major_finding_needs_corroboration_from_a_second_document():
    lone = [_signal(days_ago=1, document_id=DOC_A, magnitude="major")]
    assert build_cluster(lone) is None

    corroborated = [
        _signal(days_ago=1, document_id=DOC_A, magnitude="major"),
        _signal(days_ago=2, document_id=DOC_B, direction="neutral", magnitude="minor"),
    ]
    cluster = build_cluster(corroborated)
    assert cluster is not None
    assert any("major" in trigger for trigger in cluster.triggers)


def test_tone_reversal_against_the_baseline_triggers():
    signals = [
        _signal(days_ago=1, document_id=DOC_A, signal_type=SignalType.SENTIMENT_SHIFT,
                direction=None, magnitude=None, sentiment=-0.7),
        _signal(days_ago=2, document_id=DOC_B, signal_type=SignalType.SENTIMENT_SHIFT,
                direction=None, magnitude=None, sentiment=-0.6),
        # Baseline: the same company read positive a month ago.
        _signal(days_ago=40, document_id=DOC_B, signal_type=SignalType.SENTIMENT_SHIFT,
                direction=None, magnitude=None, sentiment=0.5),
    ]
    cluster = build_cluster(signals)
    assert cluster is not None
    assert any("tone swing" in trigger for trigger in cluster.triggers)
    assert cluster.sentiment_delta is not None and cluster.sentiment_delta < 0


def test_prior_patterns_are_not_reclustered():
    """A pattern built from patterns would compound its own conclusions."""
    signals = [
        _signal(days_ago=1, document_id=DOC_A, signal_type=SignalType.EMERGING_PATTERN),
        _signal(days_ago=2, document_id=DOC_B, signal_type=SignalType.EMERGING_PATTERN),
    ]
    assert build_cluster(signals) is None


def test_anchor_is_the_newest_finding():
    signals = [
        _signal(days_ago=5, document_id=DOC_A),
        _signal(days_ago=1, document_id=DOC_B),
        _signal(days_ago=3, document_id=DOC_B),
    ]
    cluster = build_cluster(signals)
    assert cluster is not None
    assert cluster.anchor_signal.occurred_at == NOW - timedelta(days=1)
    assert cluster.oldest_signal.occurred_at == NOW - timedelta(days=5)


def test_findings_are_ordered_oldest_first_for_the_prompt():
    """The model is being asked to read a sequence; handing it the events out
    of order would invite it to invent the wrong causality."""
    signals = [
        _signal(days_ago=1, document_id=DOC_A, quote="last"),
        _signal(days_ago=5, document_id=DOC_B, quote="first"),
        _signal(days_ago=3, document_id=DOC_B, quote="middle"),
    ]
    cluster = build_cluster(signals)
    assert cluster is not None
    assert [f["quote"] for f in cluster.as_findings()] == ["first", "middle", "last"]


# ---- anchor quote verification -----------------------------------------


def test_anchor_quote_must_match_a_supplied_quote():
    supplied = ["We expect continued pressure on margins."]
    assert verify_anchor_quote(supplied[0], supplied) == supplied[0]


def test_paraphrased_anchor_quote_is_dropped():
    """This is the one prompt handed existing quotes to choose among, so it is
    the one place a paraphrase could enter the evidence chain unnoticed."""
    supplied = ["We expect continued pressure on margins."]
    assert verify_anchor_quote("We expect margin pressure to continue.", supplied) is None


def test_missing_anchor_quote_is_allowed():
    assert verify_anchor_quote(None, ["anything"]) is None
