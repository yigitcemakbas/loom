"""Genre-relative disclosure scoring.

The artifact this module exists to remove was measurable and specific: across
Loom's corpus the finding type `new_risk_factor` is assessed negative 158 times
out of 158, so averaging finding directions scored a company down for the
number of risk factors it publishes. Six companies read as seriously troubled
on the strength of one annual report each.

The failure mode on the other side is just as bad and is easier to reach by
accident, so it is pinned hardest here: a correction that silences the routine
negatives without also noticing how little evidence is left will turn those
same six companies confidently positive on two findings each.
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.engine.brief import build_brief
from app.engine.disclosure import (
    RECURRENCE_DISCOUNT,
    DisclosureNorms,
    document_key,
    effective_findings,
    measure_norms,
    parity_weights,
    restated,
    routine_share,
)
from app.models.brief import Stance
from app.models.signal import Signal, SignalType

NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


def _sig(
    *,
    direction: str | None = "negative",
    signal_type: SignalType = SignalType.NEW_RISK_FACTOR,
    doc_subtype: str = "10-K",
    document: str | None = "doc-1",
    company: uuid.UUID | None = None,
    summary: str = "Supply concentration: a single foundry makes most of the parts.",
    days_ago: int = 5,
    magnitude: str = "moderate",
    priority: float = 0.8,
) -> Signal:
    return Signal(
        id=uuid.uuid4(),
        company_id=company or uuid.uuid4(),
        signal_type=signal_type,
        summary=summary,
        detail=summary,
        market_direction=direction,
        market_magnitude=magnitude,
        confidence=0.9,
        priority=priority,
        occurred_at=NOW - timedelta(days=days_ago),
        source_document_id=uuid.uuid5(uuid.NAMESPACE_OID, document) if document else None,
        signal_metadata={"doc_subtype": doc_subtype},
    )


def _corpus() -> list[Signal]:
    """A corpus shaped like the real one: risk factors uniformly negative,
    quotes leaning negative in filings and positive on calls."""
    corpus = []
    for i in range(40):
        corpus.append(_sig(document=f"corpus-risk-{i}", summary=f"Risk {i}: something could go wrong."))
    for i in range(20):
        corpus.append(_sig(
            direction="negative" if i < 14 else "positive",
            signal_type=SignalType.NOTABLE_QUOTE,
            document=f"corpus-quote-{i}",
            summary=f"Quote {i}: management commented on conditions.",
        ))
    for i in range(20):
        corpus.append(_sig(
            direction="positive" if i < 14 else "negative",
            signal_type=SignalType.NOTABLE_QUOTE,
            doc_subtype="earnings_call",
            document=f"corpus-call-{i}",
            summary=f"Call {i}: management commented on demand.",
        ))
    return corpus


# ---- the measurement ---------------------------------------------------


def test_a_uniformly_negative_finding_type_is_measured_as_such():
    """The whole correction rests on this number being read off the data
    rather than assumed. If risk factors are always negative, the expectation
    for one is approximately -1 and a negative one is worth approximately
    nothing."""
    norms = measure_norms(_corpus())

    expectation = norms.expected_for(_sig()).expected
    assert expectation < -0.8

    excess = norms.excess_for(_sig(direction="negative"))
    assert abs(excess) < 0.2


def test_the_same_finding_type_is_judged_differently_by_document_genre():
    """A quote pulled from an annual report and one pulled from an earnings
    call are the same extraction against opposite distributions. Scoring them
    against one table would import the genre's bias into every company."""
    norms = measure_norms(_corpus())

    from_filing = norms.expected_for(
        _sig(signal_type=SignalType.NOTABLE_QUOTE, doc_subtype="10-K")
    ).expected
    from_call = norms.expected_for(
        _sig(signal_type=SignalType.NOTABLE_QUOTE, doc_subtype="earnings_call")
    ).expected

    assert from_filing < 0 < from_call


def test_a_company_with_its_own_habit_is_judged_against_itself():
    """The dynamism that matters. Two companies filing the same words are not
    saying the same thing, and a company with enough history of its own is
    measured against that rather than against everyone."""
    gloomy = uuid.uuid4()
    corpus = _corpus()
    # This company's calls are consistently negative, against a corpus whose
    # calls are consistently positive.
    corpus += [
        _sig(
            direction="negative",
            signal_type=SignalType.NOTABLE_QUOTE,
            doc_subtype="earnings_call",
            document=f"gloomy-{i}",
            company=gloomy,
            summary=f"Gloomy call {i}: management was cautious about demand.",
        )
        for i in range(14)
    ]
    norms = measure_norms(corpus)

    theirs = norms.expected_for(
        _sig(signal_type=SignalType.NOTABLE_QUOTE, doc_subtype="earnings_call", company=gloomy)
    ).expected
    everyone = norms.expected_for(
        _sig(signal_type=SignalType.NOTABLE_QUOTE, doc_subtype="earnings_call")
    ).expected

    assert theirs < everyone


def test_excess_is_capped_at_one_step():
    """Uncapped, a positive finding in a genre expecting -0.996 is worth
    almost two observations, and four concerns against two positives came out
    'leaning positive' on the stored corpus. That is the original artifact
    inverted, not fixed."""
    norms = measure_norms(_corpus())

    surprising = norms.excess_for(_sig(direction="positive"))

    assert surprising <= 1.0


# ---- the refusal that keeps the correction honest ----------------------


def test_a_record_made_only_of_boilerplate_produces_no_verdict():
    """The case the correction creates and must then catch. Reading one annual
    report's risk section is reading the genre, not the company, so the honest
    answer is that Loom has not learned anything yet."""
    norms = measure_norms(_corpus())
    company = uuid.uuid4()
    findings = [
        _sig(company=company, document="theirs", summary=f"Risk {i}: a thing could go wrong.")
        for i in range(12)
    ] + [
        _sig(company=company, document="theirs", direction="positive",
             signal_type=SignalType.NOTABLE_QUOTE, summary="Quote: results were in line."),
    ]

    brief = build_brief(findings, now=NOW, norms=norms)

    assert brief.stance == Stance.INSUFFICIENT
    assert brief.confidence == 0.0
    assert "documents of this kind always contain" in brief.headline


def test_the_same_record_without_the_correction_reads_as_serious_concerns():
    """The behaviour being replaced, pinned so the difference is visible and a
    regression cannot pass quietly."""
    company = uuid.uuid4()
    findings = [
        _sig(company=company, document="theirs", summary=f"Risk {i}: a thing could go wrong.")
        for i in range(12)
    ]

    assert build_brief(findings, now=NOW).stance in (
        Stance.NEGATIVE, Stance.STRONG_NEGATIVE,
    )


def test_publishing_more_does_not_by_itself_move_the_verdict():
    """The user-facing statement of the whole module: a company is not marked
    down for filing a longer risk section."""
    norms = measure_norms(_corpus())
    company = uuid.uuid4()

    def record(risk_count: int) -> float:
        findings = [
            _sig(company=company, document="theirs", summary=f"Risk {i}: a thing could go wrong.")
            for i in range(risk_count)
        ] + [
            _sig(company=company, document="call", direction="positive",
                 signal_type=SignalType.NOTABLE_QUOTE, doc_subtype="earnings_call",
                 summary="Call: demand held up through the quarter."),
            _sig(company=company, document="call", direction="positive",
                 signal_type=SignalType.NOTABLE_QUOTE, doc_subtype="earnings_call",
                 summary="Call: margins improved on better pricing."),
        ]
        return build_brief(findings, now=NOW, norms=norms).evidence["direction_mean"]

    short_section = record(6)
    long_section = record(40)

    # Identical, not merely closer. A risk factor says nothing a reader did
    # not already know an annual report would say, so it is neither a vote nor
    # a dilution of the findings that do say something.
    assert abs(short_section - long_section) < 0.01


# ---- the mechanisms ----------------------------------------------------


def test_findings_from_one_document_do_not_count_as_independent():
    weights = parity_weights([_sig(document="one") for _ in range(9)])

    assert all(abs(w - 1 / 3) < 1e-9 for w in weights.values())


def test_a_lone_finding_from_its_own_document_counts_fully():
    weights = parity_weights([_sig(document="only-one")])

    assert list(weights.values()) == [1.0]


def test_findings_with_no_document_group_by_genre_and_day():
    """Findings synthesised across disclosures, and those derived from filed
    facts, carry no document. Two from the same day are one observation about
    that day rather than two."""
    a = _sig(document=None, days_ago=3)
    b = _sig(document=None, days_ago=3)
    c = _sig(document=None, days_ago=40)

    assert document_key(a) == document_key(b)
    assert document_key(a) != document_key(c)


def test_a_risk_repeated_in_a_later_filing_is_discounted():
    first = _sig(document="2024-10k", days_ago=400,
                 summary="Concentration: a single foundry manufactures most components.")
    again = _sig(document="2025-10k", days_ago=30,
                 summary="Concentration: a single foundry manufactures most components.")

    found = restated([first, again])

    assert str(again.id) in found
    # The first time a company discloses something, it is news.
    assert str(first.id) not in found


def test_the_same_risk_inside_one_filing_is_not_a_restatement():
    """Two copies in one document are one finding seen twice, which is what
    de-duplication is for. Calling it a restatement would discount the only
    time the company actually said it."""
    text = "Concentration: a single foundry manufactures most components."
    a = _sig(document="2025-10k", summary=text)
    b = _sig(document="2025-10k", summary=text)

    assert restated([a, b]) == set()


def test_restatement_reduces_evidence_without_erasing_it():
    """A company continuing to carry a risk is weak evidence the risk is live.
    Dropping it entirely would let a company bury a deteriorating situation by
    describing it in the same words every year."""
    norms = measure_norms(_corpus())
    signal = _sig(direction="positive", signal_type=SignalType.NOTABLE_QUOTE, doc_subtype="earnings_call")

    full = effective_findings([signal], norms)
    discounted = effective_findings([signal], norms, restated_ids={str(signal.id)})

    assert 0 < discounted < full
    assert abs(discounted - full * RECURRENCE_DISCOUNT) < 1e-9


def test_routine_share_ignores_document_clustering():
    """Two different questions. How much independent evidence exists carries
    the clustering correction; what share of a company's disclosure was routine
    must not, or a deeply read company reads as almost entirely boilerplate."""
    norms = measure_norms(_corpus())
    findings = [_sig(document="one", summary=f"Risk {i}: a thing.") for i in range(20)]

    assert routine_share(findings, norms) > 0.8


def test_no_norms_means_the_older_answer_rather_than_a_residual_against_nothing():
    empty = DisclosureNorms()

    assert empty.excess_for(_sig(direction="negative")) == -1.0


# ---- what the reader is told -------------------------------------------


def test_the_headline_states_the_counts_even_when_the_verdict_points_away():
    """Microsoft holds 24 concerns against 13 positives and still reads better
    than an annual report normally does. Writing 'more positives than concerns'
    over those counts would be contradicted by the list printed underneath."""
    norms = measure_norms(_corpus())
    company = uuid.uuid4()
    findings = [
        _sig(company=company, document=f"filing-{i}", summary=f"Risk {i}: a thing could go wrong.")
        for i in range(8)
    ] + [
        _sig(company=company, document=f"call-{i}", direction="positive",
             signal_type=SignalType.NOTABLE_QUOTE, doc_subtype="earnings_call",
             summary=f"Call {i}: demand and pricing both held up.")
        for i in range(4)
    ]

    brief = build_brief(findings, now=NOW, norms=norms)

    assert "8 concerns against 4 positives" in brief.headline
    assert "more positives than concerns" not in brief.headline.lower()
    assert brief.evidence["genre_adjusted"] is True
    assert brief.evidence["routine_share"] is not None


def test_the_same_words_about_a_different_figure_is_an_update_not_a_repeat():
    """The distinction the token overlap cannot see, because it discards every
    digit so that two writings of one idea can be compared on their words.

    On the stored corpus, AMD guiding to $11.2bn in one quarter and $13bn in
    the next scored the same overlap as the same risk written twice. Discounting
    the second would have thrown away the most current number Loom holds."""
    earlier = _sig(
        document="q2", days_ago=120, signal_type=SignalType.NOTABLE_QUOTE,
        summary="Revenue guidance was set at approximately 11.2 billion for the quarter.",
    )
    later = _sig(
        document="q3", days_ago=20, signal_type=SignalType.NOTABLE_QUOTE,
        summary="Revenue guidance was set at approximately 13 billion for the quarter.",
    )

    assert restated([earlier, later]) == set()


def test_the_same_words_with_no_new_figure_is_a_repeat():
    earlier = _sig(
        document="2025-10k", days_ago=400, signal_type=SignalType.NOTABLE_QUOTE,
        summary="Memory chip costs are rising and will squeeze hardware profit margins.",
    )
    later = _sig(
        document="2026-10k", days_ago=20, signal_type=SignalType.NOTABLE_QUOTE,
        summary="Memory chip costs keep rising and will squeeze hardware profit margins.",
    )

    assert str(later.id) in restated([earlier, later])


def test_a_guidance_change_is_never_a_restatement():
    """A guidance change is a change. It says something new every time it
    occurs, however much wording it shares with the last one."""
    earlier = _sig(
        document="q2", days_ago=120, signal_type=SignalType.GUIDANCE_CHANGE,
        summary="Management raised the full year outlook on stronger demand.",
    )
    later = _sig(
        document="q3", days_ago=20, signal_type=SignalType.GUIDANCE_CHANGE,
        summary="Management raised the full year outlook on stronger demand.",
    )

    assert restated([earlier, later]) == set()
