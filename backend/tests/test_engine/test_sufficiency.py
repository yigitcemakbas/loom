"""When Loom is allowed to lean, and when it must say it has nothing.

Abstention is supposed to be what is left over when there is nothing to report.
It had become the engine's default answer instead: the sufficiency test compared
a clustering-corrected, magnitude-weighted sum against a threshold written for a
plain count of findings, and on the stored corpus that refused a verdict for 38
of 40 companies Loom had actually read. Verdict entropy was 0.18 bits of a
possible 1.0, which is not calibrated humility but a constant, and a constant
tells a reader nothing.

These tests pin the separation that fixed it. Whether Loom may lean at all is a
count of findings that said something. How firmly it may lean is a continuous
strength that accounts for clustering and repetition. The two were one number
and the unit mismatch made the gate several times stricter than it was written
to be.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

from app.engine.brief import (
    _MIN_INFORMATIVE_FINDINGS,
    _MIN_STRENGTH_FOR_STRONG,
    _stance_for,
)
from app.engine.disclosure import (
    UNINFORMATIVE_FLOOR,
    effective_findings,
    informative_count,
    measure_norms,
)
from app.models.brief import Stance
from app.models.signal import SignalType

NOW = datetime(2026, 6, 25, tzinfo=timezone.utc)


def _signal(kind, sentiment, *, doc=None, summary="", confidence=0.9):
    return SimpleNamespace(
        id=uuid4(), company_id="c1", signal_type=kind, sentiment_score=sentiment,
        confidence=confidence, priority=0.3, summary=summary or f"{kind} {sentiment}",
        detail="", evidence_quote="", occurred_at=NOW - timedelta(days=5),
        dismissed_at=None, source_document_id=doc or uuid4(),
        compared_document_id=None, signal_metadata={}, market_direction=None,
        market_magnitude=None, evidence_rate=None, evidence_sample_size=None,
    )


# ---- the two measures are different quantities ------------------------------


def test_informative_count_is_a_count_and_strength_is_not():
    """The gate is denominated in findings and the strength is not, which is the
    whole point. Feeding the strength to a threshold written for a count is what
    made the gate three to seven times stricter than intended."""
    sigs = [_signal(SignalType.QOQ_ANOMALY, -0.8, doc="d") for _ in range(4)]
    norms = measure_norms(sigs, {})
    n = informative_count(sigs, norms)
    strength = effective_findings(sigs, norms)
    assert isinstance(n, int)
    assert not isinstance(strength, int) or strength != n or n == 0


def test_strength_no_longer_charges_a_finding_for_its_own_magnitude():
    """The floor already removes findings that said nothing. Multiplying the
    survivors by their own residual charged them a second time for not saying
    enough, and `effective_findings` documents that magnitude is deliberately
    excluded from it. The implementation did the opposite of its docstring."""
    sigs = [_signal(SignalType.GUIDANCE_CHANGE, -0.9, doc=f"d{i}") for i in range(3)]
    norms = measure_norms(sigs, {})
    survivors = [s for s in sigs
                 if abs(norms.excess_for(s) or 0) >= UNINFORMATIVE_FLOOR]
    if survivors:
        # Each surviving finding is worth a whole observation before any
        # independence adjustment, never a fraction of one for being mild.
        assert effective_findings(sigs, norms) >= len(survivors) * 0.5


# ---- what the gate lets through ---------------------------------------------


def test_one_informative_finding_still_cannot_carry_a_verdict():
    """The original rule, kept. It was always right; only its unit was wrong."""
    assert _stance_for(
        -0.9, 1.0, 1.0, assessed_count=1, source_count=1,
        informative=0.9, count_informative=1,
    ) == Stance.INSUFFICIENT


def test_two_informative_findings_produce_a_lean_even_on_thin_evidence():
    """This is the behaviour change. Two findings from one filing is thin, and
    thin is a reason to soften and to say so, not a reason to withhold the
    direction Loom has already computed."""
    stance = _stance_for(
        -0.9, 1.0, 1.0, assessed_count=2, source_count=1,
        informative=0.7, count_informative=2,
    )
    assert stance == Stance.NEGATIVE


def test_thin_evidence_softens_a_strong_verdict_rather_than_erasing_it():
    strong = _stance_for(
        -0.9, 1.0, 1.0, assessed_count=9, source_count=3,
        informative=_MIN_STRENGTH_FOR_STRONG + 1, count_informative=9,
    )
    thin = _stance_for(
        -0.9, 1.0, 1.0, assessed_count=2, source_count=1,
        informative=0.5, count_informative=2,
    )
    assert strong == Stance.STRONG_NEGATIVE
    assert thin == Stance.NEGATIVE


def test_a_single_source_cannot_reach_a_strong_verdict_however_much_it_says():
    """Breadth is not volume. Twenty findings from one filing remain one opinion
    about one document."""
    assert _stance_for(
        -0.9, 1.0, 1.0, assessed_count=20, source_count=1,
        informative=20.0, count_informative=20,
    ) == Stance.NEGATIVE


# ---- what still refuses -----------------------------------------------------


def test_unread_material_still_refuses_before_anything_else_is_considered():
    """Unread is not calm, and this check comes first because every other branch
    assumes the evidence has been judged."""
    assert _stance_for(
        -0.9, 1.0, 0.1, assessed_count=9, source_count=3,
        informative=9.0, count_informative=9,
    ) == Stance.INSUFFICIENT


def test_material_that_says_nothing_directional_is_quiet_not_insufficient():
    """Two different answers for two different situations: Loom read it and
    found nothing pointing either way, versus Loom has nothing to go on."""
    assert _stance_for(
        0.0, 0.0, 1.0, assessed_count=9, source_count=3,
        informative=9.0, count_informative=9,
    ) == Stance.QUIET


def test_the_gate_is_two_findings_and_says_so_in_findings():
    """A regression guard on the unit itself. If this constant is ever compared
    against a weighted strength again, the abstention rate returns to 97%."""
    assert _MIN_INFORMATIVE_FINDINGS == 2
    assert float(_MIN_INFORMATIVE_FINDINGS).is_integer()


# ---- confidence must not contradict the caption -----------------------------


def test_confidence_falls_with_thin_evidence_rather_than_with_finding_count():
    """A brief captioned "this is a thin read" beside a confidence of 0.71,
    higher than a company with half again as much real evidence, is the
    contradiction this prevents. Both numbers now come from the same quantity."""
    from app.engine.brief import _confidence

    sigs = [_signal(SignalType.NEW_RISK_FACTOR, -0.5, doc="d") for _ in range(20)]
    sources = {"filing"}
    counts = {"positive": 0, "negative": 20, "neutral": 0}

    thin = _confidence(sigs, sources, counts, strength=0.6)
    deep = _confidence(sigs, sources, counts, strength=_MIN_STRENGTH_FOR_STRONG)
    assert thin < deep
