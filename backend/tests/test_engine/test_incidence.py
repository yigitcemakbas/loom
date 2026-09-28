"""The quantity the verdict is computed from, and why it is this one.

Direction is now a property of a finding's type: a new risk factor is negative
because the risk section grew, which is a fact about the document rather than a
forecast about the share price. That fixed the semantics and broke the
measurement, because the residual was per finding — how far a finding's direction
departs from its genre's expected direction — and once direction is determined by
type that gap is identically zero. Measured on the stored corpus, the mean
absolute excess for a new risk factor was 0.0018 and none of 195 cleared the
informativeness floor. The engine returned "insufficient" on 95% of companies.

So the signal moved to incidence: how much disclosure of a kind this company's
document carries, against how much documents of that genre normally carry. That
varies, it is checkable against the filings, and it is bidirectional without
needing a resolved-risk-factor signal, because fewer concerns than the genre
expects reads better.
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.engine.disclosure import (
    MIN_DOCUMENTS_FOR_GENRE,
    INCIDENCE_PRIOR_WEIGHT,
    NORM_PRIOR_WEIGHT,
    incidence_excess,
    measure_incidence,
)
from app.models.signal import Signal, SignalType

NOW = datetime(2026, 9, 28, tzinfo=timezone.utc)


def _sig(*, document: str, company: uuid.UUID | None = None,
         signal_type: SignalType = SignalType.NEW_RISK_FACTOR,
         sentiment: float | None = None, doc_subtype: str = "10-K") -> Signal:
    return Signal(
        id=uuid.uuid4(),
        company_id=company or uuid.uuid4(),
        signal_type=signal_type,
        summary="A thing.",
        sentiment_score=sentiment,
        confidence=0.9,
        priority=0.4,
        occurred_at=NOW - timedelta(days=3),
        source_document_id=uuid.uuid5(uuid.NAMESPACE_OID, document),
        signal_metadata={"doc_subtype": doc_subtype},
    )


def _corpus(risk_per_filing: int = 10, filings: int = 12) -> list[Signal]:
    out = []
    for d in range(filings):
        for i in range(risk_per_filing):
            out.append(_sig(document=f"10k-{d}"))
    return out


# ---- the norm ---------------------------------------------------------------


def test_the_norm_is_per_document_not_per_company():
    """Ten risk factors in one filing and ten across two filings are different
    facts about disclosure volume, and the unit has to be the document or the
    measurement becomes a measurement of how much Loom happened to read."""
    norms = measure_incidence(_corpus(risk_per_filing=10, filings=12))

    assert norms.by_genre["10-K"].documents == 12
    assert 8.0 < norms.by_genre["10-K"].negative < 10.5


def test_a_genre_with_too_few_documents_is_not_trusted():
    """Same principle as MIN_SECTOR_OBSERVATIONS: a cell resting on one document
    is not an expectation, and reading it as one would judge every company
    against whichever filing happened to be read first."""
    thin = measure_incidence([_sig(document="only-one")])

    expected = thin.expected_for("10-K")
    assert thin.by_genre["10-K"].documents < MIN_DOCUMENTS_FOR_GENRE
    assert expected is thin.root


def test_counts_are_shrunk_more_lightly_than_directions():
    """A per-document count is stable inside a genre and wildly different between
    genres, so shrinking an annual report's expected eleven risk factors toward a
    corpus mean that includes earnings calls carrying one imports the wrong
    genre's answer rather than expressing caution. At the direction weight a
    perfectly ordinary annual report scored half a point negative."""
    assert INCIDENCE_PRIOR_WEIGHT < NORM_PRIOR_WEIGHT


# ---- the residual ----------------------------------------------------------


def test_more_disclosure_than_the_genre_reads_worse():
    norms = measure_incidence(_corpus())
    company = uuid.uuid4()
    heavy = [_sig(document="theirs", company=company) for _ in range(30)]

    residual, workings = incidence_excess(heavy, norms)

    assert residual is not None and residual < -0.2
    assert workings["measurable"] == 1


def test_less_disclosure_than_the_genre_reads_better():
    """The positive channel. Loom cannot see a risk factor being resolved — the
    diff only ever looks for paragraphs in the current filing absent from the
    prior one, never the reverse — so without this the stance would be negative
    by construction, which is what had reader agents shorting into rallies."""
    norms = measure_incidence(_corpus())
    company = uuid.uuid4()
    light = [_sig(document="theirs", company=company) for _ in range(3)]

    residual, _ = incidence_excess(light, norms)

    assert residual is not None and residual > 0.2


def test_a_filing_at_the_genre_volume_reads_as_neither():
    norms = measure_incidence(_corpus(risk_per_filing=10, filings=12))
    company = uuid.uuid4()
    ordinary = [_sig(document="theirs", company=company) for _ in range(10)]

    residual, _ = incidence_excess(ordinary, norms)

    assert residual is not None and abs(residual) < 0.15


def test_the_residual_is_bounded():
    """A single extraordinary filing cannot saturate the scale and carry a
    verdict on its own."""
    norms = measure_incidence(_corpus())
    company = uuid.uuid4()
    absurd = [_sig(document="theirs", company=company) for _ in range(400)]

    residual, _ = incidence_excess(absurd, norms)

    assert residual == -1.0


# ---- the guard that stops absence reading as good news ---------------------


def test_a_document_with_no_directional_finding_is_not_evidence_of_a_clean_filing():
    """The failure this prevents was measured: a filing carrying six quotes and
    no risk factors scored against a genre expecting five came out strongly
    positive. That is only a signal if the risk diff actually ran, and for a
    company with one stored filing it cannot — there is no prior to compare
    against. Thirty-six of the forty-six companies Loom has read are in that
    position, so without this guard most of the corpus would earn a positive
    verdict out of missing data."""
    norms = measure_incidence(_corpus())
    company = uuid.uuid4()
    quotes_only = [
        _sig(document="theirs", company=company, signal_type=SignalType.NOTABLE_QUOTE)
        for _ in range(6)
    ]

    residual, workings = incidence_excess(quotes_only, norms)

    assert residual is None
    assert workings["measurable"] == 0


def test_documents_are_averaged_so_one_filing_cannot_speak_for_three():
    norms = measure_incidence(_corpus())
    company = uuid.uuid4()
    findings = (
        [_sig(document="heavy", company=company) for _ in range(30)]
        + [_sig(document="light-a", company=company) for _ in range(3)]
        + [_sig(document="light-b", company=company) for _ in range(3)]
    )

    residual, workings = incidence_excess(findings, norms)

    assert workings["measurable"] == 3
    # Two ordinary-to-light filings pull the one heavy filing back toward the
    # middle rather than being drowned by its finding count.
    heavy_only, _ = incidence_excess(
        [_sig(document="heavy", company=company) for _ in range(30)], norms
    )
    assert residual > heavy_only
