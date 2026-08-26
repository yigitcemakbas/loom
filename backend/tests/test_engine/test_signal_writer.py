"""Signal construction tests. No database, no network, these check the
translation from model output to storable signals."""

import uuid
from datetime import datetime, timezone

from app.engine.clustering import Cluster
from app.engine.prompts.emerging_pattern import EmergingPatternResult
from app.engine.signal_writer import (
    build_diff_signals,
    build_document_signals,
    build_pattern_signal,
)
from app.engine.prompts.document_analysis import (
    DocumentAnalysisResult,
    ExtractedQuote,
    ExtractedRisk,
    GuidanceChange,
)
from app.engine.prompts.market_reaction import MarketReaction
from app.engine.prompts.risk_diff import RiskAssessment
from app.models.signal import Signal, SignalType

COMPANY = uuid.uuid4()
DOC = uuid.uuid4()
PRIOR_DOC = uuid.uuid4()
WHEN = datetime(2026, 8, 1, tzinfo=timezone.utc)

_NEGATIVE_REACTION = MarketReaction(
    direction="negative", magnitude="moderate", horizon="near_term",
    rationale="Margin pressure of this scale typically weighs on shares near-term.",
)

RESULT = DocumentAnalysisResult(
    sentiment_score=-0.4,
    sentiment_summary="Management hedged on margin guidance.",
    confidence=0.8,
    notable_quotes=[
        ExtractedQuote(
            quote="We expect continued pressure.", why_it_matters="Margin risk.",
            market_reaction=_NEGATIVE_REACTION,
        )
    ],
    key_risks=[
        ExtractedRisk(
            label="supplier concentration", quote="We rely on one supplier.",
            why_it_matters="Single point of failure.", market_reaction=_NEGATIVE_REACTION,
        )
    ],
    guidance_change=GuidanceChange(description="Full-year outlook lowered.", market_reaction=_NEGATIVE_REACTION),
)


def _build(used_sections: bool = True):
    return build_document_signals(
        RESULT, company_id=COMPANY, document_id=DOC, occurred_at=WHEN, used_sections=used_sections
    )


def test_each_finding_becomes_a_signal():
    types = [s.signal_type for s in _build()]
    assert SignalType.SENTIMENT_SHIFT in types
    assert SignalType.NOTABLE_QUOTE in types
    assert SignalType.NEW_RISK_FACTOR in types
    assert SignalType.GUIDANCE_CHANGE in types


def test_quotes_and_risks_carry_their_evidence():
    """A signal without its supporting text cannot be verified by the reader."""
    for signal in _build():
        if signal.signal_type in (SignalType.NOTABLE_QUOTE, SignalType.NEW_RISK_FACTOR):
            assert signal.evidence_quote, f"{signal.signal_type} has no evidence"


def test_market_reaction_is_threaded_onto_the_signal():
    """The whole point of this field: it must actually reach the stored row,
    not just exist in the LLM response object."""
    risk_signal = next(s for s in _build() if s.signal_type == SignalType.NEW_RISK_FACTOR)
    assert risk_signal.market_direction == "negative"
    assert risk_signal.market_magnitude == "moderate"
    assert risk_signal.market_horizon == "near_term"
    assert risk_signal.detail == _NEGATIVE_REACTION.rationale


def test_sentiment_shift_has_no_market_reaction_block():
    """Sentiment already states its own directional read in the summary;
    it doesn't get a second, separate market_reaction sub-object."""
    sentiment_signal = next(s for s in _build() if s.signal_type == SignalType.SENTIMENT_SHIFT)
    assert sentiment_signal.market_direction is None


def test_unfocused_input_is_discounted():
    """Analysis over a whole document is noisier than over its relevant
    sections, and should not be trusted equally."""
    focused = _build(used_sections=True)[0].confidence
    unfocused = _build(used_sections=False)[0].confidence
    assert unfocused < focused


def test_sentiment_is_clamped_to_range():
    extreme = RESULT.model_copy(update={"sentiment_score": -7.0})
    signals = build_document_signals(
        extreme, company_id=COMPANY, document_id=DOC, occurred_at=WHEN, used_sections=True
    )
    sentiment = next(s for s in signals if s.signal_type == SignalType.SENTIMENT_SHIFT)
    assert sentiment.sentiment_score == -1.0


# ---- emerging pattern (short-window synthesis) -------------------------

_QUOTE = "We expect component costs to rise through the year."


def _cluster() -> Cluster:
    older = Signal(
        id=uuid.uuid4(), company_id=COMPANY, signal_type=SignalType.NEW_RISK_FACTOR,
        summary="cost pressure", confidence=0.9, priority=0.5,
        occurred_at=datetime(2026, 7, 28, tzinfo=timezone.utc),
        source_document_id=PRIOR_DOC, evidence_quote=_QUOTE, signal_metadata={},
    )
    newer = Signal(
        id=uuid.uuid4(), company_id=COMPANY, signal_type=SignalType.NEW_RISK_FACTOR,
        summary="margin risk", confidence=0.9, priority=0.5,
        occurred_at=datetime(2026, 8, 1, tzinfo=timezone.utc),
        source_document_id=DOC, evidence_quote=None, signal_metadata={},
    )
    return Cluster(
        signals=[older, newer], document_ids=[PRIOR_DOC, DOC],
        window_days=7, triggers=["3 findings aligned negative"],
        dominant_direction="negative",
    )


def _pattern_result(**overrides) -> EmergingPatternResult:
    base = dict(
        is_coherent=True,
        headline="Cost pressure now confirmed across two filings in four days.",
        narrative="Two separate disclosures point at the same margin squeeze.",
        anchor_quote=_QUOTE,
        confidence=0.85,
        market_reaction=_NEGATIVE_REACTION,
    )
    base.update(overrides)
    return EmergingPatternResult(**base)


def test_incoherent_findings_produce_no_signal():
    """Companies file unrelated things in the same week. The deterministic gate
    cannot tell coincidence from pattern, so the model is given an explicit way
    to say 'unrelated' and it has to be honoured."""
    assert build_pattern_signal(_pattern_result(is_coherent=False), _cluster(), company_id=COMPANY) is None


def test_pattern_spans_its_cluster():
    signal = build_pattern_signal(_pattern_result(), _cluster(), company_id=COMPANY)

    assert signal is not None
    assert signal.signal_type == SignalType.EMERGING_PATTERN
    # Anchored on the newest finding, compared against the oldest.
    assert signal.source_document_id == DOC
    assert signal.compared_document_id == PRIOR_DOC
    assert signal.signal_metadata["window_days"] == 7


def test_detail_is_the_narrative_without_the_rationale_appended():
    """The narrative already explains the direction for this type; appending
    the market-reaction rationale measurably restated it."""
    signal = build_pattern_signal(_pattern_result(), _cluster(), company_id=COMPANY)

    assert signal is not None
    assert signal.detail == "Two separate disclosures point at the same margin squeeze."
    assert signal.market_direction == "negative"


def test_paraphrased_anchor_quote_never_becomes_evidence():
    """The synthesis prompt is the one place a model picks among quotes it was
    handed, so it is the one place a paraphrase could enter the evidence chain."""
    signal = build_pattern_signal(
        _pattern_result(anchor_quote="We think costs will go up."), _cluster(), company_id=COMPANY
    )

    assert signal is not None
    assert signal.evidence_quote is None


def test_verbatim_anchor_quote_is_kept():
    signal = build_pattern_signal(_pattern_result(), _cluster(), company_id=COMPANY)

    assert signal is not None
    assert signal.evidence_quote == _QUOTE


def test_non_substantive_diffs_are_dropped():
    """Showing cosmetic rewrites would train the reader to ignore the feed."""
    assessments = [
        RiskAssessment(quote="Genuinely new dependency.", is_substantive=True,
                       label="new dependency", why_it_matters="Concentration risk.", confidence=0.9,
                       market_reaction=_NEGATIVE_REACTION),
        RiskAssessment(quote="Same risk, new wording.", is_substantive=False,
                       label="rewording", why_it_matters="No change.", confidence=0.9),
    ]
    signals = build_diff_signals(
        assessments, company_id=COMPANY, document_id=DOC,
        compared_document_id=PRIOR_DOC, occurred_at=WHEN,
    )
    assert len(signals) == 1
    assert signals[0].evidence_quote == "Genuinely new dependency."
    assert signals[0].market_direction == "negative"
    # A comparison signal must record what it was compared against.
    assert signals[0].compared_document_id == PRIOR_DOC
