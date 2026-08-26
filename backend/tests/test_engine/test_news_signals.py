"""Batched news digest tests.

Batching is a real risk to the project's core invariant, that every signal
points at the document it came from. One call now covers fifteen documents, and
the only thing tying a finding back to its source is an index the model
returned. These tests pin down that the mapping is correct and that a bad index
loses the finding rather than misattributing it.
"""

import uuid
from datetime import datetime, timezone

from app.engine.prompts.market_reaction import MarketReaction
from app.engine.prompts.news_digest import NewsDigestResult, NewsFinding
from app.engine.signal_writer import build_news_signals
from app.models.signal import SignalType

COMPANY = uuid.uuid4()

_REACTION = MarketReaction(
    direction="negative", magnitude="moderate", horizon="near_term",
    rationale="A supplier price rise of this kind pressures near-term margins.",
)


class _Doc:
    """Minimal stand-in for RawDocument: the writer only reads these fields."""

    def __init__(self, day: int):
        self.id = uuid.uuid4()
        self.published_at = datetime(2026, 8, day, tzinfo=timezone.utc)
        self.fetched_at = self.published_at


DOCS = [_Doc(1), _Doc(5), _Doc(9)]


def _result(findings, **overrides) -> NewsDigestResult:
    base = dict(
        sentiment_score=-0.3,
        sentiment_summary="Coverage turned cautious across the week.",
        confidence=0.8,
        findings=findings,
    )
    base.update(overrides)
    return NewsDigestResult(**base)


def _finding(index: int) -> NewsFinding:
    return NewsFinding(
        item_index=index,
        summary="Supplier raised prices.",
        quote="The supplier raised prices this quarter.",
        market_reaction=_REACTION,
    )


def test_findings_attach_to_the_item_they_came_from():
    """The whole risk of batching: index 2 must become document two, not the
    newest one, and not the batch as a whole."""
    signals = build_news_signals(_result([_finding(2)]), DOCS, company_id=COMPANY)

    finding_signal = next(s for s in signals if s.signal_type == SignalType.NOTABLE_QUOTE)
    assert finding_signal.source_document_id == DOCS[1].id
    assert finding_signal.occurred_at == DOCS[1].published_at


def test_out_of_range_indexes_are_dropped_not_misattributed():
    """A signal on the wrong document is worse than a missing one: it would
    send a reader to a filing that never said it."""
    signals = build_news_signals(
        _result([_finding(0), _finding(99), _finding(-1)]), DOCS, company_id=COMPANY
    )

    assert all(s.signal_type != SignalType.NOTABLE_QUOTE for s in signals)


def test_the_run_gets_one_sentiment_signal_on_the_newest_item():
    """Tone is a read on the whole window, so it belongs to the window's most
    recent item rather than being duplicated across every one."""
    signals = build_news_signals(_result([]), DOCS, company_id=COMPANY)

    sentiment = [s for s in signals if s.signal_type == SignalType.SENTIMENT_SHIFT]
    assert len(sentiment) == 1
    assert sentiment[0].source_document_id == DOCS[-1].id
    assert sentiment[0].signal_metadata["batched_items"] == 3


def test_an_all_noise_run_still_reports_tone():
    """Most weeks produce no findings. That is the prompt working, and the
    ticker should still get its tone read rather than nothing at all."""
    signals = build_news_signals(_result([]), DOCS, company_id=COMPANY)

    assert len(signals) == 1


def test_market_reaction_survives_batching():
    signals = build_news_signals(_result([_finding(1)]), DOCS, company_id=COMPANY)

    finding_signal = next(s for s in signals if s.signal_type == SignalType.NOTABLE_QUOTE)
    assert finding_signal.market_direction == "negative"
    assert finding_signal.detail == _REACTION.rationale
    assert finding_signal.evidence_quote == "The supplier raised prices this quarter."


def test_sentiment_is_clamped():
    signals = build_news_signals(_result([], sentiment_score=-4.0), DOCS, company_id=COMPANY)

    assert signals[0].sentiment_score == -1.0
