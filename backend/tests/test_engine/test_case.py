"""The case file is a ranking, so its tests are about order.

Every panel it draws from was already correct on its own. What this module adds
is the judgement about which of thirty things a reader should see first, and a
reader scanning a list stops near the top. Getting the order wrong does not
produce a visible error; it produces a page that looks complete and buries the
thing that mattered.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from app.engine.case import (
    LIVE_EVENT_DAYS,
    WEIGHT_CONTRADICTION,
    build_case,
)

NOW = datetime(2026, 9, 22, tzinfo=timezone.utc)


@dataclass
class _Contradiction:
    key: str = "tone_vs_cash"
    headline: str = "Management sounds confident, the cash does not agree."
    says_better: str = "Tone reads positive."
    says_worse: str = "Accruals are among the worst."
    why_it_matters: str = "Accruals are where judgement lives."


@dataclass
class _Factor:
    key: str
    percentile: float
    label: str = "A measure"
    meaning: str = "What it means."
    source: str = "A paper."


@dataclass
class _Finding:
    id: str = "f1"
    summary: str = "A risk was disclosed."
    detail: str = "Some detail."
    market_direction: Optional[str] = "negative"
    market_magnitude: Optional[str] = "moderate"
    evidence_rate: Optional[float] = None
    evidence_quote: Optional[str] = None
    signal_metadata: Optional[dict] = None


@dataclass
class _Event:
    id: str = "e1"
    headline: str = "Confirms a standing concern."
    direction: str = "negative"
    form: str = "8-K"
    occurred_at: datetime = NOW


@dataclass
class _Brief:
    class _Stance:
        value = "negative"
    stance = _Stance()
    headline: str = "More concerns than positives."
    confidence: float = 0.6


def _case(**kwargs):
    kwargs.setdefault("ticker", "AAA")
    kwargs.setdefault("name", "A Company")
    kwargs.setdefault("now", NOW)
    return build_case(**kwargs)


# ---- the ordering is the product --------------------------------------


def test_disagreement_outranks_every_single_reading():
    """When two independent sources point opposite ways, that is the moment
    worth attention, and it is the one thing no screener can produce."""
    case = _case(
        contradictions=[_Contradiction()],
        factors=[_Factor(key="accruals", percentile=0.01)],
        findings=[_Finding(market_magnitude="major")],
    )
    assert case.points[0].key.startswith("contradiction:")
    assert case.points[0].weight == WEIGHT_CONTRADICTION


def test_an_unusual_finding_outranks_a_louder_routine_one():
    """A "major" risk from a company that files one every quarter describes its
    disclosure habits. The same label from one that never has is news."""
    routine = _Finding(id="routine", summary="Routine major risk.", market_magnitude="major")
    unusual = _Finding(id="unusual", summary="Uncharacteristic risk.",
                       market_magnitude="moderate", evidence_rate=0.02)

    case = _case(findings=[routine, unusual])
    assert case.points[0].headline.startswith("Uncharacteristic")


def test_rarity_is_explained_rather_than_only_ranked():
    """A reader shown something first is owed the reason it is first."""
    case = _case(findings=[_Finding(evidence_rate=0.03)])
    assert "3%" in case.points[0].detail


def test_middling_factor_readings_are_left_out_entirely():
    """A case file is not a data dump. The middle of a distribution is not
    saying anything for a reader to act on."""
    case = _case(factors=[_Factor(key="accruals", percentile=0.5)])
    assert not [p for p in case.points if p.key.startswith("factor:")]


def test_a_stale_matched_filing_is_not_presented_as_news():
    """The fast path exists because the window is short. A two month old match
    is history."""
    old = _Event(occurred_at=NOW - timedelta(days=LIVE_EVENT_DAYS + 5))
    fresh = _Event(id="e2", occurred_at=NOW - timedelta(days=1))

    case = _case(events=[old, fresh])
    events = [p for p in case.points if p.key.startswith("event:")]
    assert len(events) == 1
    assert "yesterday" in events[0].detail


# ---- what the case refuses to do --------------------------------------


def test_a_contradiction_is_never_given_a_side():
    """It says Loom's evidence is inconsistent. Giving it a direction would
    invent the synthesis it exists to avoid."""
    case = _case(contradictions=[_Contradiction()])
    assert case.points[0].side == "unclear"


def test_valuation_is_kept_apart_from_the_business_case():
    """They answer different questions, and a reader conflating them buys a
    good company at any price."""
    case = _case(factors=[
        _Factor(key="earnings_yield", percentile=0.02),
        _Factor(key="accruals", percentile=0.02),
    ])
    assert [p.key for p in case.valuation] == ["factor:earnings_yield"]
    assert [p.key for p in case.points] == ["factor:accruals"]


def test_the_case_never_contradicts_the_verdict_it_explains():
    """Nothing here recomputes a judgement. A third opinion would leave the
    reader with no way to know which to believe."""
    case = _case(brief=_Brief(), findings=[_Finding(market_direction="positive")])
    assert case.stance == "negative"
    assert case.headline == "More concerns than positives."


# ---- the other side ----------------------------------------------------


def test_the_strongest_opposing_point_is_surfaced_separately():
    """A reader who sees only the case for a conclusion is reading a sales
    pitch, and it is the point a decision most needs."""
    case = _case(
        brief=_Brief(),  # negative stance
        findings=[
            _Finding(id="weak", summary="A mild positive.", market_direction="positive"),
            _Finding(id="strong", summary="A strong positive.",
                     market_direction="positive", market_magnitude="major"),
            _Finding(id="neg", summary="A concern.", market_direction="negative"),
        ],
    )
    against = case.strongest_against
    assert against is not None and against.headline == "A strong positive."


def test_there_is_no_opposing_point_when_nothing_opposes():
    case = _case(brief=_Brief(), findings=[_Finding(market_direction="negative")])
    assert case.strongest_against is None


# ---- absence is stated -------------------------------------------------


def test_an_unread_company_says_so_rather_than_looking_complete():
    """A page that looks finished when it is not is the most misleading thing
    this module could produce."""
    case = _case(factors=[_Factor(key="accruals", percentile=0.02)])
    assert any("has not read" in gap for gap in case.gaps)


def test_a_company_with_no_standing_view_says_so():
    case = _case(findings=[_Finding()])
    assert any("no standing view" in gap for gap in case.gaps)


def test_a_company_with_no_valuation_says_price_is_not_accounted_for():
    case = _case(findings=[_Finding()])
    assert any("what you would be paying" in gap for gap in case.gaps)


def test_a_fully_covered_company_reports_no_gaps():
    case = _case(
        findings=[_Finding()],
        factors=[_Factor(key="earnings_yield", percentile=0.95)],
        prior=type("P", (), {"watch_items": [{"topic": "A thing"}]})(),
    )
    assert case.gaps == []


def test_an_empty_company_still_produces_a_readable_case():
    case = _case()
    assert case.ticker == "AAA"
    assert case.points == []
    assert case.headline.startswith("Loom has not formed a view")


# ---- length, and what price means -------------------------------------


def test_a_case_is_capped_at_a_readable_length():
    """A ranked list of fifty is a scroll rather than an argument. A reader
    stops near the top, so everything past the first dozen costs attention and
    returns nothing."""
    from app.engine.case import MAX_POINTS

    many = [_Finding(id=f"f{i}", summary=f"Finding {i}.") for i in range(40)]
    case = _case(findings=many)
    assert len(case.points) == MAX_POINTS


def test_the_withheld_count_is_reported_rather_than_hidden():
    """A case built from fifty findings and one built from twelve are different
    objects, and silently truncating makes them look identical."""
    from app.engine.case import MAX_POINTS

    many = [_Finding(id=f"f{i}", summary=f"Finding {i}.") for i in range(30)]
    case = _case(findings=many)
    assert any(f"{30 - MAX_POINTS} further findings" in gap for gap in case.gaps)


def test_valuation_is_shown_even_when_unremarkable():
    """What you would pay is relevant at every level, not only when it is
    extreme. A reader shown no price at all assumes price does not matter."""
    case = _case(factors=[_Factor(key="earnings_yield", percentile=0.5)])
    assert [p.key for p in case.valuation] == ["factor:earnings_yield"]


def test_an_unremarkable_valuation_does_not_claim_a_side():
    case = _case(factors=[_Factor(key="earnings_yield", percentile=0.5)])
    assert case.valuation[0].side == "unclear"


def test_an_unremarkable_business_measure_is_still_left_out():
    """The exception is price, not everything."""
    case = _case(factors=[_Factor(key="accruals", percentile=0.5)])
    assert not [p for p in case.points if p.key.startswith("factor:")]


def test_price_is_only_reported_missing_when_it_could_not_be_computed():
    """Saying "Loom could not work out what you would be paying" because a
    reading was merely ordinary would be false."""
    case = _case(factors=[_Factor(key="earnings_yield", percentile=0.5)])
    assert not any("what you would be paying" in gap for gap in case.gaps)
