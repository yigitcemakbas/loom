"""The backfill's one dangerous failure mode is silent misattribution.

Every other thing that can go wrong here is loud: a refused key, an exhausted
quota, an unparseable response. Writing one company's market-reaction judgement
onto another company's finding is none of those. It produces a plausible row
that no later check would question, so the alignment rule gets its own tests.
"""

from datetime import datetime, timezone

import pytest

from app.models.signal import Signal, SignalType
from scripts.backfill_reactions import BACKFILL_TAG, IndexedReaction, _apply, align


def _finding(summary: str, **kwargs) -> Signal:
    return Signal(
        signal_type=SignalType.NEW_RISK_FACTOR,
        summary=summary,
        confidence=0.7,
        priority=1.0,
        occurred_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        signal_metadata={},
        **kwargs,
    )


def _reaction(index: int, horizon: str = "multi_quarter") -> IndexedReaction:
    return IndexedReaction(
        index=index, direction="negative", magnitude="moderate",
        horizon=horizon, rationale="Because of the disclosure.",
    )


def test_pairs_each_judgement_with_its_own_finding():
    batch = [_finding("first"), _finding("second"), _finding("third")]
    pairs, refused = align(batch, [_reaction(3), _reaction(1), _reaction(2)])

    assert refused == []
    # Returned out of order on purpose: the index is what binds them, not
    # position in the response.
    assert [(s.summary, r.index) for s, r in pairs] == [
        ("third", 3), ("first", 1), ("second", 2),
    ]


@pytest.mark.parametrize("bad_index", [0, -1, 4, 99])
def test_refuses_an_index_outside_the_batch(bad_index):
    batch = [_finding("first"), _finding("second")]
    pairs, refused = align(batch, [_reaction(bad_index)])

    assert pairs == []
    assert refused == [bad_index]


def test_refuses_a_repeated_index_rather_than_overwriting():
    batch = [_finding("first"), _finding("second")]
    pairs, refused = align(batch, [_reaction(1), _reaction(1), _reaction(2)])

    assert refused == [1]
    assert [s.summary for s, _ in pairs] == ["first", "second"]


def test_applies_all_three_tags_and_records_the_backfill():
    signal = _finding("a risk")
    _apply(signal, _reaction(1, horizon="structural"), now=datetime(2026, 9, 21, tzinfo=timezone.utc))

    assert signal.market_horizon == "structural"
    assert signal.market_direction == "negative"
    assert signal.market_magnitude == "moderate"
    # Recorded so a backtest can tell a judgement made at extraction time from
    # one made months later.
    assert signal.signal_metadata[BACKFILL_TAG]["method"] == "finding_text_only"


def test_does_not_overwrite_a_detail_the_finding_already_has():
    signal = _finding("a risk", detail="Something the extraction already said.")
    _apply(signal, _reaction(1), now=datetime(2026, 9, 21, tzinfo=timezone.utc))

    assert signal.detail == "Something the extraction already said."


def test_fills_an_empty_detail_with_the_rationale():
    signal = _finding("a risk")
    _apply(signal, _reaction(1), now=datetime(2026, 9, 21, tzinfo=timezone.utc))

    assert signal.detail == "Because of the disclosure."
