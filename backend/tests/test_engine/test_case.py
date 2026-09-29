"""The case file is a ranking, so its tests are about order.

Every panel it draws from was already correct on its own. What this module adds
is the judgement about which of thirty things a reader should see first, and a
reader scanning a list stops near the top. Getting the order wrong does not
produce a visible error; it produces a page that looks complete and buries the
thing that mattered.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
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
class _Standing:
    summary: str = "The shares are up 12% over the past year."
    range_position: float | None = 0.5
    drawdown: float | None = 0.1
    near_high: bool = False
    near_low: bool = False


@dataclass
class _Move:
    abnormal_percent: float = -14.0
    sessions: int = 10
    as_of: date = date(2026, 9, 1)
    sigma: float | None = -2.6
    summary: str = "In the fortnight after this was filed, the shares fell 14.0% against the market."
    short: str = "after which the shares fell 14.0% against the market"
    standalone: str = (
        "The most recent filing Loom read here was followed by the shares falling 14.0%."
    )
    is_material: bool = True
    is_striking: bool = False


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
        standing=_Standing(),
    )
    assert case.gaps == []


def test_a_company_with_no_price_history_says_so():
    """Without it, nothing on the page accounts for what the market has already
    done with the same information, and the page does not look any different."""
    case = _case(
        findings=[_Finding()],
        factors=[_Factor(key="earnings_yield", percentile=0.95)],
        prior=type("P", (), {"watch_items": [{"topic": "A thing"}]})(),
    )
    assert any("what the market has already done" in gap for gap in case.gaps)


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


def test_the_withheld_points_are_reachable_rather_than_merely_counted():
    """What changed after the agent trial. Every case file announced that
    thirty to sixty further findings existed and offered no way to see them,
    which two readers independently called out: being told evidence is being
    held back and given no means to reach it is a reason to distrust the
    twelve that were shown, not a mark of honesty.

    So they are returned, in rank order, and the interface opens them on
    request. The count is still derivable, and now so is the content."""
    from app.engine.case import MAX_POINTS

    many = [_Finding(id=f"f{i}", summary=f"Finding {i}.") for i in range(30)]
    case = _case(findings=many)

    assert len(case.points) == MAX_POINTS
    assert len(case.withheld) == 30 - MAX_POINTS
    # Still ranked, and still ranked below everything shown.
    assert max(p.weight for p in case.withheld) <= min(p.weight for p in case.points)


def test_nothing_is_withheld_from_a_short_case():
    case = _case(findings=[_Finding(id="f1", summary="One finding.")])

    assert case.withheld == []


def test_the_best_objection_is_found_even_when_it_ranked_off_the_list():
    """The one slot the cap must not decide. A company whose twelve strongest
    points all argue one way is exactly the case where the best argument
    against has been ranked off, and showing nothing there reads as "there is
    no argument the other way" rather than "it did not fit"."""
    from app.engine.case import MAX_POINTS

    supporting = [
        _Finding(id=f"f{i}", summary=f"Positive finding {i}.", market_direction="positive",
                 market_magnitude="major")
        for i in range(MAX_POINTS + 6)
    ]
    objection = _Finding(
        id="objection", summary="The one concern.", market_direction="negative",
    )

    positive_brief = _Brief()
    positive_brief.stance = type("S", (), {"value": "positive"})()
    case = _case(brief=positive_brief, findings=[*supporting, objection])

    assert all(p.key != "finding:objection" for p in case.points)
    assert case.strongest_against is not None
    assert case.strongest_against.key == "finding:objection"


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


def test_one_story_is_one_row():
    """Three filings can each match the same standing expectation and each
    arrives with its own id, so three identical rows reached the list. It was
    invisible while everything past the twelfth sat behind a count; it is the
    first thing a reader sees now that the rest is reachable.

    The same failure was fixed in the change feed for the same reason, and
    walking into it twice is what makes it worth a test rather than a comment.
    """
    same = [
        _Event(id=f"e{i}", headline="AAA: confirms 3 standing concerns", occurred_at=NOW)
        for i in range(3)
    ]

    case = _case(events=same)

    headlines = [p.headline for p in (*case.points, *case.withheld)]
    assert len(headlines) == len(set(headlines))


def test_a_headline_does_not_repeat_the_ticker_on_that_company_page():
    """Event headlines carry the ticker because they are written for a feed
    covering every company. Here it is the heading of the page."""
    case = _case(events=[_Event(headline="AAA: confirms a standing concern", occurred_at=NOW)])

    assert any(p.headline == "confirms a standing concern" for p in case.points)


# ---- what the price already did ---------------------------------------


def test_a_finding_the_market_repriced_outranks_one_it_ignored():
    """The only confirmation in the whole list that comes from outside Loom.
    A filing the market moved on is one an independent party, with money at
    stake, agreed said something."""
    from app.engine.case import WEIGHT_MARKET_MOVED

    moved = _Finding(id="moved", summary="The filing the market repriced.")
    ignored = _Finding(id="ignored", summary="The filing nobody reacted to.")

    case = _case(
        findings=[moved, ignored],
        moves={"moved": _Move()},
        standing=_Standing(),
    )

    by_key = {p.key: p for p in case.points}
    assert by_key["finding:moved"].weight == WEIGHT_MARKET_MOVED
    assert by_key["finding:ignored"].weight < WEIGHT_MARKET_MOVED


def test_the_market_ignoring_a_finding_carries_no_penalty():
    """The subtle version of letting price forecast, and the easier one to walk
    into. Discounting a finding because nobody priced it reads as calibration
    and is not: a disclosure the market has not reacted to is the only kind
    Loom can add anything to, and burying those would delete the reason to read
    filings at all."""
    alone = _case(findings=[_Finding(id="f1")], standing=_Standing())
    with_prices = _case(findings=[_Finding(id="f1")], moves={}, standing=_Standing())

    assert alone.points[0].weight == with_prices.points[0].weight


def test_price_is_never_an_argument_for_or_against():
    """Treating a rising share as a point in a company's favour is momentum
    wearing a verdict's clothes, which is predictive in exactly the sense this
    project refuses."""
    case = _case(findings=[_Finding()], standing=_Standing())

    assert case.price
    assert all(p.side == "unclear" for p in case.price)


def test_price_context_is_kept_out_of_the_argument():
    """A separate block, because it is not a reason. It is the condition the
    reasons are read in."""
    case = _case(findings=[_Finding()], standing=_Standing())

    assert all(not p.key.startswith("price:") for p in (*case.points, *case.withheld))


def test_the_market_reaction_is_stated_once_not_on_every_row():
    """One annual report yields forty findings and all of them measure the
    same fortnight. The full sentence appeared forty times on a single page,
    which is the fact repeated rather than the fact reported."""
    findings = [_Finding(id=f"f{i}", summary=f"Finding {i}.") for i in range(6)]
    case = _case(
        findings=findings,
        moves={f"f{i}": _Move() for i in range(6)},
        standing=_Standing(),
    )

    full_sentence = sum(
        1 for p in (*case.points, *case.withheld, *case.price)
        if "The most recent filing Loom read here" in (p.headline + p.detail)
    )
    assert full_sentence == 1

    # The fact still rides on each row, as a clause on the source line.
    rows = [p for p in case.points if p.key.startswith("finding:")]
    assert rows and all("against the market" in p.source for p in rows)


def test_a_topics_calibration_is_shown_once_and_on_the_highest_ranked_row():
    """Apple's case carries fifteen separate findings about costs and margins.
    Attaching the calibration to each printed the same two-line paragraph
    fifteen times down one page, which is the sentence repeated rather than the
    sentence read.

    It goes on the highest-ranked row of that topic, which is not known until
    the ranking exists, so it is applied after sorting rather than during
    assembly."""
    quiet = _Finding(id="quiet", summary="An ordinary cost finding.")
    loud = _Finding(id="loud", summary="A serious cost finding.", market_magnitude="major")
    third = _Finding(id="third", summary="Another cost finding.")
    precedent = type("P", (), {
        "topic": "margin_cost",
        "summary": "Of the 21 comparable disclosures Loom has read, nothing much followed.",
    })()

    case = _case(
        findings=[quiet, loud, third],
        precedents={"quiet": precedent, "loud": precedent, "third": precedent},
    )

    carrying = [
        p for p in (*case.points, *case.withheld)
        if "comparable disclosures" in p.detail
    ]
    assert len(carrying) == 1
    assert carrying[0].key == "finding:loud"


def test_different_topics_each_get_their_own_calibration():
    cost = _Finding(id="cost", summary="A cost finding.")
    trade = _Finding(id="trade", summary="A tariff finding.")

    def _p(topic):
        return type("P", (), {"topic": topic, "summary": f"Comparable disclosures about {topic}."})()

    case = _case(findings=[cost, trade], precedents={"cost": _p("margin_cost"), "trade": _p("trade")})

    carrying = [p for p in case.points if "Comparable disclosures" in p.detail]
    assert len(carrying) == 2


def test_a_refusal_has_no_argument_to_contradict():
    """A verdict has to point somewhere before anything can point against it.

    Every stance that was not positive got treated as negative, so a company
    Loom had explicitly declined to judge displayed "the best argument the
    other way" directly beneath its own "not enough read yet". On Coca-Cola
    that surfaced a *favourable* point labelled as the objection to it."""
    for undirected in ("insufficient", "quiet", "mixed"):
        brief = _Brief()
        brief.stance = type("S", (), {"value": undirected})()
        case = _case(brief=brief, findings=[
            _Finding(id="pos", summary="Something good.", market_direction="positive"),
            _Finding(id="neg", summary="Something bad.", market_direction="negative"),
        ])
        assert case.strongest_against is None, undirected


def test_a_directional_verdict_still_shows_its_best_objection():
    brief = _Brief()
    brief.stance = type("S", (), {"value": "positive"})()
    case = _case(brief=brief, findings=[
        _Finding(id="pos", summary="Something good.", market_direction="positive"),
        _Finding(id="neg", summary="Something bad.", market_direction="negative"),
    ])

    assert case.strongest_against is not None
    assert case.strongest_against.side == "against"


def test_routine_findings_are_marked_so_a_long_list_is_not_misread():
    """Apple reads positive and the points below the cut run 26 against to 14
    for. A reader counting them concludes the verdict contradicts its own
    evidence. It does not: most of those 26 are risk factors every annual
    report contains, which the stance already scored at approximately zero.
    The reader had no way to see that."""
    boilerplate = _Finding(id="boiler", summary="A risk every filing carries.")
    real = _Finding(id="real", summary="Something specific happened.")

    case = _case(findings=[boilerplate, real], routine_ids={"boiler"})

    by_key = {p.key: p for p in (*case.points, *case.withheld)}
    assert by_key["finding:boiler"].routine is True
    assert by_key["finding:real"].routine is False


def test_nothing_is_marked_routine_without_measured_norms():
    """No norms means no judgement about what is ordinary, and guessing would
    label real findings as boilerplate."""
    case = _case(findings=[_Finding(id="f1")])

    assert all(p.routine is False for p in (*case.points, *case.withheld))


# ---- what the case rests on -------------------------------------------


def test_an_unread_company_says_what_loom_does_know():
    """The brief is computed from findings alone, so a company Loom has never
    read carried "not enough analysed yet" however much else was known about
    it. On a page already showing twelve ranked measures, a valuation and a
    year of price history, that answers a question nobody asked and hides the
    answer to the one they did."""
    brief = _Brief()
    brief.stance = type("S", (), {"value": "insufficient"})()

    case = _case(
        brief=brief,
        findings=[],
        factors=[_Factor(key="earnings_yield", percentile=0.4), _Factor(key="accruals", percentile=0.05)],
        standing=_Standing(),
        peers=231,
        sector="Technology",
    )

    assert "has not read" in case.headline
    assert "reported numbers" in case.headline
    # The detail belongs to the basis line, which renders directly beneath the
    # headline. Putting it in both made the top of the page say the same thing
    # twice before saying anything useful.
    assert "2 measures" not in case.headline
    assert "2 measures" in case.basis.summary
    assert "231 technology companies" in case.basis.summary


def test_the_stance_itself_is_untouched_by_what_the_numbers_say():
    """There is genuinely no direction, and inventing one from the numbers is
    exactly what this project refuses. Only the sentence changes."""
    brief = _Brief()
    brief.stance = type("S", (), {"value": "insufficient"})()

    case = _case(
        brief=brief, findings=[],
        factors=[_Factor(key="accruals", percentile=0.02)],
        peers=231, sector="Technology",
    )

    assert case.stance == "insufficient"
    assert case.strongest_against is None


def test_a_read_company_keeps_the_verdict_it_earned():
    brief = _Brief()   # negative, with a headline of its own
    case = _case(brief=brief, findings=[_Finding()], peers=231, sector="Technology")

    assert case.headline == "More concerns than positives."
    assert case.basis.is_read is True


def test_a_company_loom_holds_nothing_on_says_so():
    brief = _Brief()
    brief.stance = type("S", (), {"value": "insufficient"})()

    case = _case(brief=brief, findings=[], factors=[])

    assert case.basis.summary.startswith("Loom holds almost nothing")


def test_the_basis_counts_only_factors_that_computed():
    """A factor that could not be computed has said nothing, and counting it
    would inflate what the page claims to rest on."""
    case = _case(
        findings=[],
        factors=[_Factor(key="accruals", percentile=0.3), _Factor(key="momentum", percentile=None)],
        peers=100, sector="Utilities",
    )

    assert case.basis.factors == 1


# ---- what a company depends on -----------------------------------------------


def test_a_dependency_with_news_outranks_a_standing_dependency():
    """The ordering is the product here. "A company you depend on filed something
    today" belongs near the top; a structural relationship with nothing attached is
    context. Both are shown, at different weights."""
    from app.engine.case import WEIGHT_CONTEXT, WEIGHT_DEPENDENCY, build_case

    with_news = build_case(
        ticker="AMD", name="AMD",
        exposures=[("NVDA", 136, "NVDA filed an 8-K on datacenter demand.")],
    )
    standing = build_case(ticker="AMD", name="AMD", exposures=[("NVDA", 136, None)])

    live = next(p for p in with_news.points if p.key == "dependency:NVDA")
    quiet = next(p for p in standing.points if p.key == "dependency:NVDA")

    assert live.weight == WEIGHT_DEPENDENCY > quiet.weight == WEIGHT_CONTEXT


def test_a_dependency_argues_in_no_direction():
    """A supplier filing an 8-K is a reason to look. Calling it bullish or bearish
    would invent the synthesis engine/contradiction.py refuses for the same
    reason."""
    from app.engine.case import build_case

    case = build_case(
        ticker="AMD", name="AMD", exposures=[("NVDA", 136, "NVDA filed an 8-K.")]
    )

    point = next(p for p in case.points if p.key == "dependency:NVDA")
    assert point.side == "unclear"
    assert point.settles_it


def test_a_dependency_says_why_the_edge_is_evidence_rather_than_a_guess():
    """The edge is read out of the filer's own risk factors, where material
    dependencies must be disclosed. That provenance is the whole reason this
    outranks a sector label, so the reader is told it."""
    from app.engine.case import build_case

    case = build_case(ticker="AMD", name="AMD", exposures=[("NVDA", 136, None)])

    point = next(p for p in case.points if p.key == "dependency:NVDA")
    assert "136" in point.detail
    assert "NVDA" in point.source
