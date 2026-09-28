"""The half of the risk comparison that did not exist.

`find_changed_paragraphs` extracted Item 1A from both filings and iterated the
*current* one looking for paragraphs with no close match in the prior. It never
looked the other way, so Loom could see a risk appear and never see one resolve.

That was not a missing nicety. It meant every finding the risk diff could produce
was negative, and the stance had no route to improvement except the company
saying something reassuring in a different section entirely — a structural
negative bias in the one signal `priority.py` trusts most. Measured on Apple's two
most recent annual reports: 39 paragraphs added, 45 withdrawn, and the withdrawn
half invisible.

These tests pin both directions, and pin the asymmetry in how they are judged: a
withdrawn paragraph is weaker evidence than an added one, because a merge, a
reorganisation or a trim by counsel looks identical to a resolution from outside
the document.
"""

import uuid
from datetime import datetime, timezone

from app.engine import diffing, signal_writer
from app.engine.direction import can_be_directional, documentary_sign
from app.engine.priority import TYPE_WEIGHTS
from app.engine.prompts.market_reaction import MarketReaction
from app.engine.prompts.risk_resolution import ResolutionAssessment
from app.models.signal import SignalType

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)

_SHARED = (
    "We face intense competition across all of our markets and our competitors "
    "may have greater resources, which could reduce our margins over time."
)
_ADDED = (
    "Our largest customer has notified us of its intention to terminate its "
    "supply agreement, which would materially reduce revenue in the next year."
)
_WITHDRAWN = (
    "Pending litigation in Ireland concerning historic tax assessments remains "
    "unresolved and an adverse outcome could require a material payment."
)


# Shared filler. `extract_section` requires at least twenty lines before it will
# treat a passage as a section at all, which is correct for real filings and means
# a fixture cannot be two paragraphs long. These appear identically in both
# filings, so they are unchanged in either direction and never show up in a diff.
_FILLER = [
    f"The Company depends on arrangement number {i} for part of its operations, "
    f"and a disruption there could materially harm its operating results."
    for i in range(22)
]


def _filing(*paragraphs: str) -> str:
    """A filing shaped the way `extract_section` needs one.

    The leading bare "Item 1A." / "Item 1B." pair is the table of contents: the
    extractor deliberately takes the *second* occurrence, because the first is the
    index entry rather than the section. Matching the fixture in test_diffing.py
    rather than inventing a second shape.
    """
    return "\n".join(
        ["Item 1A.", "Item 1B."]
        + ["Item 1A.    Risk Factors"]
        + _FILLER
        + list(paragraphs)
        + ["Item 1B.    Unresolved Staff Comments"]
    )


# ---- the diff itself --------------------------------------------------------


def test_the_diff_reports_both_directions():
    diff = diffing.diff_section(
        _filing(_ADDED), _filing(_WITHDRAWN), section="1A"
    )

    assert any("largest customer" in p for p in diff.added)
    assert any("Ireland" in p for p in diff.removed)
    # Paragraphs both filings carry are not a change in either direction.
    assert not any("arrangement number" in p for p in diff.added)
    assert not any("arrangement number" in p for p in diff.removed)


def test_a_withdrawn_paragraph_is_no_longer_invisible():
    """The regression this exists to prevent. Before the fix this list was always
    empty, whatever the filings said."""
    diff = diffing.diff_section(
        _filing(), _filing(_WITHDRAWN), section="1A"
    )

    assert diff.removed
    assert not diff.added


def test_the_additions_only_view_still_means_what_it_meant():
    """`find_changed_paragraphs` has callers and tests that want additions alone,
    and the symmetric result would silently change their meaning."""
    added, current_total, prior_total = diffing.find_changed_paragraphs(
        _filing(_ADDED), _filing(_WITHDRAWN), section="1A"
    )

    assert any("largest customer" in p for p in added)
    assert not any("Ireland" in p for p in added)
    assert current_total == prior_total == len(_FILLER) + 1


def test_both_directions_are_capped_against_a_restructured_section():
    """A filer rewriting the whole section must not turn into one enormous
    prompt, in either direction."""
    many = [
        f"Risk number {i}: the Company depends on a distinct arrangement number {i} "
        f"whose failure could materially harm its operating results in that market."
        for i in range(120)
    ]
    other = [
        f"Unrelated exposure {i}: the Company holds a separate obligation numbered "
        f"{i} which if called upon would require a material and unbudgeted payment."
        for i in range(120)
    ]

    diff = diffing.diff_section(_filing(*many), _filing(*other), section="1A")

    assert len(diff.added) <= diffing.MAX_PARAGRAPHS_TO_ASSESS
    assert len(diff.removed) <= diffing.MAX_PARAGRAPHS_TO_ASSESS


# ---- what a resolution means -----------------------------------------------


def test_a_resolved_risk_points_up():
    """The only documentary finding from a filing that does. Without it the
    stance cannot improve on the evidence the risk comparison produces."""
    from types import SimpleNamespace

    signal = SimpleNamespace(
        signal_type=SignalType.RESOLVED_RISK_FACTOR,
        signal_metadata={}, sentiment_score=None,
    )

    assert documentary_sign(signal) == 1.0
    assert can_be_directional(signal)


def test_a_resolution_is_trusted_slightly_less_than_an_addition():
    """Both are established by comparing two filings and both are checkable
    against source text, so they belong in the same band. A resolution sits just
    below because judging that a risk is *gone* is harder than judging that one
    appeared: a merged or reorganised paragraph is indistinguishable from a
    resolution without reading the whole section again."""
    assert (
        TYPE_WEIGHTS[SignalType.RESOLVED_RISK_FACTOR]
        < TYPE_WEIGHTS[SignalType.NEW_RISK_FACTOR]
    )
    assert TYPE_WEIGHTS[SignalType.RESOLVED_RISK_FACTOR] > TYPE_WEIGHTS[
        SignalType.SENTIMENT_SHIFT
    ]


def test_a_resolution_reaches_the_stance():
    """It is worth asserting separately. A signal type absent from
    _SUBSTANTIVE_TYPES produces evidence the verdict never sees, which is how a
    positive channel can exist in the extractor and still not exist in the
    product."""
    from app.engine.brief import _SUBSTANTIVE_TYPES

    assert SignalType.RESOLVED_RISK_FACTOR in _SUBSTANTIVE_TYPES


# ---- the judgement ---------------------------------------------------------


def _assessment(*, resolved: bool) -> ResolutionAssessment:
    return ResolutionAssessment(
        quote=_WITHDRAWN,
        is_resolved=resolved,
        label="Ireland tax assessment",
        why_it_matters="A contingency the company no longer considers worth disclosing.",
        confidence=0.8,
        market_reaction=(
            MarketReaction(direction="positive", magnitude="moderate",
                           horizon="multi_quarter",
                           rationale="A resolved contingency removes an overhang.")
            if resolved else None
        ),
    )


def test_only_genuine_resolutions_become_signals():
    """Crediting a company with resolving a risk its lawyers merely consolidated
    reads better than the filings support, and that is the more dangerous of the
    two errors: a missed resolution costs the reader nothing."""
    signals = signal_writer.build_resolution_signals(
        [_assessment(resolved=True), _assessment(resolved=False)],
        company_id=uuid.uuid4(), document_id=uuid.uuid4(),
        compared_document_id=uuid.uuid4(), occurred_at=NOW,
    )

    assert len(signals) == 1
    assert signals[0].signal_type == SignalType.RESOLVED_RISK_FACTOR


def test_a_resolution_keeps_the_receipt_for_the_filing_it_came_from():
    """The quoted text exists only in the prior filing, so without the compared
    document id the evidence would point at a document that does not contain
    it."""
    document, prior = uuid.uuid4(), uuid.uuid4()

    signal = signal_writer.build_resolution_signals(
        [_assessment(resolved=True)],
        company_id=uuid.uuid4(), document_id=document,
        compared_document_id=prior, occurred_at=NOW,
    )[0]

    assert signal.evidence_quote == _WITHDRAWN
    assert signal.source_document_id == document
    assert signal.compared_document_id == prior
    assert signal.signal_metadata["withdrawn_from_prior_filing"] is True
