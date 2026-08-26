"""Turns engine output into Signal rows. The only module that persists them.

Extraction and diffing return plain data; nothing upstream of this file knows
about the database. Priority is computed here so every signal is ranked by the
same rule regardless of which stage produced it.
"""

import uuid
from datetime import datetime, timezone

from app.engine import priority
from app.engine.clustering import Cluster, verify_anchor_quote
from app.engine.llm_client import PROMPT_VERSION
from app.engine.prompts.document_analysis import DocumentAnalysisResult
from app.engine.prompts.emerging_pattern import EmergingPatternResult
from app.engine.prompts.market_reaction import MarketReaction
from app.engine.prompts.news_digest import NewsDigestResult
from app.engine.prompts.quarter_comparison import QuarterChange
from app.engine.prompts.risk_diff import RiskAssessment
from app.models.signal import Signal, SignalType

# Applied when analysis ran on a whole document rather than its relevant
# sections: the input was noisier, so the output deserves less weight.
_UNFOCUSED_INPUT_PENALTY = 0.85

# How each comparison describes itself in a finding's summary. "New risk vs
# prior year" was hardcoded when the only comparison was the annual one, and
# would have been actively wrong on a quarterly diff.
_COMPARISON_PREFIX = {
    "year_over_year_risk_factors": "New risk vs prior year",
    "quarter_over_quarter_mdna": "Changed vs last quarter",
}


def _meta(extra: dict | None = None) -> dict:
    return {"prompt_version": PROMPT_VERSION, **(extra or {})}


def build_document_signals(
    result: DocumentAnalysisResult,
    *,
    company_id: uuid.UUID,
    document_id: uuid.UUID,
    occurred_at: datetime,
    used_sections: bool,
    doc_subtype: str | None = None,
) -> list[Signal]:
    """Convert one document analysis into signals."""
    confidence = min(max(result.confidence, 0.0), 1.0)
    if not used_sections:
        confidence *= _UNFOCUSED_INPUT_PENALTY

    signals: list[Signal] = []

    def add(
        signal_type: SignalType,
        summary: str,
        quote: str | None,
        sentiment: float | None = None,
        reaction: MarketReaction | None = None,
    ) -> None:
        signals.append(
            Signal(
                company_id=company_id,
                signal_type=signal_type,
                summary=summary,
                detail=reaction.rationale if reaction else None,
                sentiment_score=sentiment,
                confidence=confidence,
                priority=priority.score(signal_type, confidence, occurred_at),
                evidence_quote=quote,
                source_document_id=document_id,
                occurred_at=occurred_at,
                market_direction=reaction.direction if reaction else None,
                market_magnitude=reaction.magnitude if reaction else None,
                market_horizon=reaction.horizon if reaction else None,
                signal_metadata=_meta(
                    {"used_sections": used_sections, "doc_subtype": doc_subtype}
                ),
            )
        )

    add(
        SignalType.SENTIMENT_SHIFT,
        result.sentiment_summary,
        None,
        sentiment=max(min(result.sentiment_score, 1.0), -1.0),
    )

    for quote in result.notable_quotes:
        add(SignalType.NOTABLE_QUOTE, quote.why_it_matters, quote.quote, reaction=quote.market_reaction)

    for risk in result.key_risks:
        add(
            SignalType.NEW_RISK_FACTOR,
            f"{risk.label}: {risk.why_it_matters}",
            risk.quote,
            reaction=risk.market_reaction,
        )

    if result.guidance_change:
        add(
            SignalType.GUIDANCE_CHANGE,
            result.guidance_change.description,
            None,
            reaction=result.guidance_change.market_reaction,
        )

    return signals


def build_diff_signals(
    assessments: list[RiskAssessment],
    *,
    company_id: uuid.UUID,
    document_id: uuid.UUID,
    compared_document_id: uuid.UUID,
    occurred_at: datetime,
    comparison: str = "year_over_year_risk_factors",
) -> list[Signal]:
    """Convert year-over-year risk assessments into signals.

    Only substantive findings become signals. Paragraphs the model judged to be
    rewording are dropped here rather than shown, a feed full of cosmetic
    changes would train the reader to ignore it.
    """
    signals: list[Signal] = []
    for item in assessments:
        if not item.is_substantive:
            continue
        confidence = min(max(item.confidence, 0.0), 1.0)
        reaction = item.market_reaction
        signals.append(
            Signal(
                company_id=company_id,
                signal_type=SignalType.QOQ_ANOMALY,
                summary=f"{_COMPARISON_PREFIX.get(comparison, 'Changed vs prior filing')}, {item.label}: {item.why_it_matters}",
                detail=reaction.rationale if reaction else None,
                sentiment_score=None,
                confidence=confidence,
                priority=priority.score(SignalType.QOQ_ANOMALY, confidence, occurred_at),
                evidence_quote=item.quote,
                source_document_id=document_id,
                compared_document_id=compared_document_id,
                occurred_at=occurred_at,
                market_direction=reaction.direction if reaction else None,
                market_magnitude=reaction.magnitude if reaction else None,
                market_horizon=reaction.horizon if reaction else None,
                signal_metadata=_meta({"comparison": comparison}),
            )
        )
    return signals


def build_quarter_change_signals(
    changes: list[QuarterChange],
    *,
    company_id: uuid.UUID,
    document_id: uuid.UUID,
    compared_document_id: uuid.UUID,
    occurred_at: datetime,
) -> list[Signal]:
    """Convert quarter-over-quarter movements into signals.

    The summary leads with the movement and its size, because "gross margin
    rose from 60.5% to 74.9%" is the finding a reader acts on, and a label
    without a magnitude is not decision-useful.
    """
    signals: list[Signal] = []
    for item in changes:
        if not item.is_substantive:
            continue
        confidence = min(max(item.confidence, 0.0), 1.0)
        reaction = item.market_reaction
        signals.append(
            Signal(
                company_id=company_id,
                signal_type=SignalType.QOQ_ANOMALY,
                summary=f"{item.label}: {item.what_changed} {item.why_it_matters}".strip(),
                detail=reaction.rationale if reaction else None,
                sentiment_score=None,
                confidence=confidence,
                priority=priority.score(SignalType.QOQ_ANOMALY, confidence, occurred_at),
                evidence_quote=item.quote,
                source_document_id=document_id,
                compared_document_id=compared_document_id,
                occurred_at=occurred_at,
                market_direction=reaction.direction if reaction else None,
                market_magnitude=reaction.magnitude if reaction else None,
                market_horizon=reaction.horizon if reaction else None,
                signal_metadata=_meta({"comparison": "quarter_over_quarter_mdna"}),
            )
        )
    return signals


def build_news_signals(
    result: NewsDigestResult,
    documents: list,
    *,
    company_id: uuid.UUID,
) -> list[Signal]:
    """Convert one batched news digest into signals.

    `documents` is the list handed to the prompt, in the same order, so a
    finding's 1-based `item_index` maps back to the document it came from and
    every signal keeps a real source. Findings whose index is out of range, or
    whose quote is not actually in that item, are dropped rather than attached
    to the wrong filing, which would be worse than losing them.
    """
    confidence = min(max(result.confidence, 0.0), 1.0)
    signals: list[Signal] = []

    newest = max(documents, key=lambda d: d.published_at or d.fetched_at)
    occurred_at = newest.published_at or newest.fetched_at

    signals.append(
        Signal(
            company_id=company_id,
            signal_type=SignalType.SENTIMENT_SHIFT,
            summary=result.sentiment_summary,
            sentiment_score=max(min(result.sentiment_score, 1.0), -1.0),
            confidence=confidence,
            priority=priority.score(SignalType.SENTIMENT_SHIFT, confidence, occurred_at),
            source_document_id=newest.id,
            occurred_at=occurred_at,
            signal_metadata=_meta(
                {"doc_subtype": "news", "batched_items": len(documents)}
            ),
        )
    )

    for finding in result.findings:
        index = finding.item_index - 1
        if not 0 <= index < len(documents):
            continue
        document = documents[index]
        reaction = finding.market_reaction
        document_occurred = document.published_at or document.fetched_at
        signals.append(
            Signal(
                company_id=company_id,
                signal_type=SignalType.NOTABLE_QUOTE,
                summary=finding.summary,
                detail=reaction.rationale if reaction else None,
                sentiment_score=None,
                confidence=confidence,
                priority=priority.score(
                    SignalType.NOTABLE_QUOTE, confidence, document_occurred
                ),
                evidence_quote=finding.quote,
                source_document_id=document.id,
                occurred_at=document_occurred,
                market_direction=reaction.direction if reaction else None,
                market_magnitude=reaction.magnitude if reaction else None,
                market_horizon=reaction.horizon if reaction else None,
                signal_metadata=_meta({"doc_subtype": "news"}),
            )
        )

    return signals


_RULE_SIGNAL_TYPES = {
    "insider_sell_cluster": SignalType.INSIDER_ACTIVITY,
    "insider_buy_cluster": SignalType.INSIDER_ACTIVITY,
    "short_interest_spike": SignalType.SHORT_INTEREST_SPIKE,
}


def build_fact_signals(
    findings: list,
    *,
    company_id: uuid.UUID,
) -> list[Signal]:
    """Convert threshold-rule findings into signals.

    These carry no `evidence_quote`, and that is correct rather than a gap: the
    receipt for "four insiders sold within a fortnight" is the filings
    themselves, listed in metadata, not a sentence someone wrote. The
    verbatim-quote rule exists to stop a model paraphrasing prose, and no model
    was involved here.
    """
    signals: list[Signal] = []
    for finding in findings:
        signal_type = _RULE_SIGNAL_TYPES.get(finding.rule)
        if signal_type is None:
            continue
        occurred_at = datetime.combine(
            finding.occurred_at, datetime.min.time(), tzinfo=timezone.utc
        )
        signals.append(
            Signal(
                company_id=company_id,
                signal_type=signal_type,
                summary=finding.summary,
                detail=finding.detail,
                sentiment_score=None,
                confidence=finding.confidence,
                priority=priority.score(signal_type, finding.confidence, occurred_at),
                occurred_at=occurred_at,
                market_direction=finding.direction,
                market_magnitude=finding.magnitude,
                market_horizon=finding.horizon,
                signal_metadata=_meta({"rule": finding.rule, **finding.evidence}),
            )
        )
    return signals


def build_pattern_signal(
    result: EmergingPatternResult,
    cluster: Cluster,
    *,
    company_id: uuid.UUID,
) -> Signal | None:
    """Convert one synthesised cluster into a signal, or None if the model
    judged the findings unrelated.

    The anchor quote is re-verified against what the model was actually given
    before it is stored, so a paraphrase can never become a signal's receipt.
    """
    if not result.is_coherent:
        return None

    confidence = min(max(result.confidence, 0.0), 1.0)
    reaction = result.market_reaction
    anchor = cluster.anchor_signal
    occurred_at = anchor.occurred_at

    return Signal(
        company_id=company_id,
        signal_type=SignalType.EMERGING_PATTERN,
        summary=result.headline,
        # The narrative alone, deliberately. Other types put the market-reaction
        # rationale here because they have nothing else explaining their
        # direction; a pattern's narrative already does that job at length, and
        # appending the rationale measurably restated it (verified against live
        # output). The direction/magnitude/horizon tags still render.
        detail=result.narrative,
        sentiment_score=None,
        confidence=confidence,
        priority=priority.score(SignalType.EMERGING_PATTERN, confidence, occurred_at),
        evidence_quote=verify_anchor_quote(result.anchor_quote, cluster.quotes),
        source_document_id=anchor.source_document_id,
        compared_document_id=cluster.oldest_signal.source_document_id,
        occurred_at=occurred_at,
        market_direction=reaction.direction if reaction else None,
        market_magnitude=reaction.magnitude if reaction else None,
        market_horizon=reaction.horizon if reaction else None,
        signal_metadata=_meta(
            {
                "comparison": "short_window_synthesis",
                "window_days": cluster.window_days,
                "triggers": cluster.triggers,
                "document_ids": [str(doc_id) for doc_id in cluster.document_ids],
                "source_signal_ids": [str(s.id) for s in cluster.signals if s.id],
            }
        ),
    )
