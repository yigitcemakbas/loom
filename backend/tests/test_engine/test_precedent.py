"""What has usually followed a disclosure like this one.

A reader who meets "a single foundry manufactures most of our components" has
no way to calibrate it: it is written in the register of a serious problem, it
sits under a heading called Risk Factors, and nothing says whether disclosures
of that kind have been followed by anything at all.

The danger in answering that question is obvious and is what most of this file
tests. A base rate built from too few cases, or from one company's repeated
filings, or from the subject company's own history, reads exactly like a base
rate built from enough. The reader cannot tell the difference by eye, so the
refusals have to be in the code.
"""

from app.engine.precedent import (
    DIRECTIONAL_SHARE,
    MIN_CASES,
    MIN_COMPANIES,
    MIN_SECTOR_CASES,
    Case,
    measure_precedents,
    topics_of,
)


def _cases(
    n: int,
    *,
    topic: str = "margin_cost",
    sector: str | None = "Technology",
    move: float = -6.0,
    companies: int | None = None,
    prefix: str = "C",
) -> list[Case]:
    companies = companies if companies is not None else n
    return [
        Case(topic=topic, sector=sector, ticker=f"{prefix}{i % companies}", move_percent=move)
        for i in range(n)
    ]


def _spread(n: int, **kw) -> list[Case]:
    """Cases that went both ways, which is what the real corpus looks like."""
    out = []
    for i in range(n):
        out.extend(_cases(1, move=(-9.0 if i % 2 else 11.0), prefix=f"S{i}", **kw))
    return out


# ---- the refusals ------------------------------------------------------


def test_too_few_cases_produces_no_precedent():
    """"Of the 3 comparable disclosures Loom has read" gives a number the shape
    of evidence without the substance, and a reader cannot discount it by eye."""
    base = measure_precedents(_cases(MIN_CASES - 1))

    assert base.lookup("margin_cost", "Technology") is None


def test_enough_cases_from_too_few_companies_produces_no_precedent():
    """Twenty cases drawn from three companies is three companies' experience
    repeated, and a precedent is supposed to be about what happens generally."""
    base = measure_precedents(_cases(MIN_CASES + 8, companies=MIN_COMPANIES - 1))

    assert base.lookup("margin_cost", "Technology") is None


def test_an_unknown_topic_produces_no_precedent():
    base = measure_precedents(_cases(30))

    assert base.lookup("cyber", "Technology") is None


# ---- the company cannot be its own precedent ---------------------------


def test_the_subject_company_is_excluded_from_its_own_precedent():
    """A precedent built partly from Apple's history, shown against an Apple
    finding, is Apple predicting Apple. At these sample sizes one firm with six
    filings can be half the evidence."""
    theirs = _cases(20, move=-20.0, companies=20, prefix="OTHER")
    own = _cases(20, move=+20.0, companies=1, prefix="SUBJ")

    base = measure_precedents(theirs + own)
    found = base.lookup("margin_cost", "Technology", exclude_ticker="SUBJ0")

    assert found is not None
    assert found.cases == 20
    assert found.median_percent < 0


def test_excluding_the_company_can_leave_too_little_to_say():
    """And when it does, the answer is nothing rather than a thinner claim."""
    base = measure_precedents(_cases(MIN_CASES + 2, companies=2, prefix="SUBJ"))

    assert base.lookup("margin_cost", "Technology", exclude_ticker="SUBJ0") is None


# ---- a direction is claimed only against a bar -------------------------


def test_cases_that_went_both_ways_are_not_reported_as_a_tendency():
    """The normal outcome on the stored corpus. Every one of the fourteen
    topics produces a t-statistic between +0.18 and +1.28 once clustered by
    filing, so the honest output is almost always that the cases say nothing."""
    base = measure_precedents(_spread(MIN_CASES + 10))
    found = base.lookup("margin_cost", "Technology")

    assert found is not None
    assert not found.is_directional
    assert "has not, by itself, told you which way" in found.summary


def test_the_spread_is_always_reported_alongside_the_middle():
    """A median of -1% across cases running from -9% to +11% is not a tendency,
    and showing the median alone presents it as one."""
    base = measure_precedents(_spread(MIN_CASES + 10))
    found = base.lookup("margin_cost", "Technology")

    assert found.low_percent < 0 < found.high_percent
    assert "running from" in found.summary


def test_a_genuinely_one_sided_record_is_reported_as_one():
    """The mechanism has to be able to fire, or the refusals above are just a
    disabled feature wearing a rigorous explanation."""
    consistent = [
        Case(topic="margin_cost", sector="Technology", ticker=f"C{i}",
             move_percent=-8.0 + (i % 5) * 0.4)
        for i in range(30)
    ]

    found = measure_precedents(consistent).lookup("margin_cost", "Technology")

    assert found is not None
    assert found.is_directional
    assert found.share_negative >= DIRECTIONAL_SHARE
    assert "fell afterwards in" in found.summary


def test_outcomes_with_no_spread_are_the_strongest_evidence_not_the_weakest():
    """A zero denominator made thirty identical outcomes read as no evidence at
    all, because the t-statistic came out 0.0 and every caller reads that as
    "indistinguishable from nothing". The same mistake is recorded against
    `calculate_z_score` in the statistics package.

    Real price moves never have zero spread, so nothing reaches this. A branch
    that would report perfect consistency as no evidence is wrong whether or
    not anything reaches it."""
    identical = measure_precedents(_cases(30, move=-8.0, companies=30))
    found = identical.lookup("margin_cost", "Technology")

    assert found.t_statistic is None
    assert found.is_directional


def test_a_near_zero_middle_is_described_rather_than_printed():
    """Printing "-0.2%" invites a reader to treat two tenths of a percent,
    inside a twenty point spread, as a finding."""
    mixed = _cases(15, move=-0.3, companies=15, prefix="A") + _cases(
        15, move=+0.3, companies=15, prefix="B"
    )
    found = measure_precedents(mixed).lookup("margin_cost", "Technology")

    assert "went nowhere in particular" in found.summary


# ---- sector against everything -----------------------------------------


def test_a_thin_sector_falls_back_to_every_company():
    """Below its own floor, "what happened at technology companies" is a
    smaller and noisier version of "what happened", not a sharper one."""
    tech = _cases(MIN_SECTOR_CASES - 5, sector="Technology", companies=12, prefix="T")
    rest = _cases(40, sector="Health Care", companies=40, prefix="H")

    found = measure_precedents(tech + rest).lookup("margin_cost", "Technology")

    assert found is not None
    assert found.sector is None
    assert "at other companies" in found.summary


def test_a_sector_with_enough_of_its_own_is_preferred():
    tech = _cases(MIN_SECTOR_CASES + 5, sector="Technology", companies=25, prefix="T")
    rest = _cases(40, sector="Health Care", companies=40, prefix="H")

    found = measure_precedents(tech + rest).lookup("margin_cost", "Technology")

    assert found.sector == "Technology"
    assert "at other technology companies" in found.summary


# ---- the wording -------------------------------------------------------


def test_the_summary_never_claims_to_speak_for_history():
    """The record behind this is one engine's reading of twenty-odd companies
    over about a year. Dressing that as history would be the most misleading
    thing this module could do."""
    found = measure_precedents(_spread(MIN_CASES + 10)).lookup("margin_cost", "Technology")

    assert "Loom has read" in found.summary
    for overclaim in ("history shows", "historically", "will ", "expect"):
        assert overclaim not in found.summary.lower()


def test_topics_are_matched_by_what_a_filing_actually_says():
    assert topics_of("Rising memory chip costs squeeze hardware margins") == {"margin_cost"}
    assert "trade" in topics_of("New tariffs on imported components")
    assert topics_of("The weather was fine") == set()


def test_one_disclosure_can_be_about_two_things():
    """A disclosure about tariffs raising component costs is genuinely about
    both, and forcing a single label discards half of what it said."""
    assert topics_of("Tariffs raise component costs and compress margins") >= {"trade", "margin_cost"}


def test_the_topic_shown_is_the_one_the_text_is_most_about():
    """Only one precedent can go on a row, and picking arbitrarily from a set
    does exactly what it sounds like: a finding headed "Trade disputes and
    international conflict" was calibrated against the supply-concentration
    record because set iteration order decided it."""
    from app.engine.precedent import primary_topic

    trade = (
        "Trade disputes and international conflict: changes in tariff policy and new "
        "export controls, along with further sanctions, raise geopolitical exposure."
    )

    assert primary_topic(trade) == "trade"


def test_the_choice_of_topic_is_reproducible():
    """Ties broken by the fixed order the topics are declared in, so the same
    text always calibrates against the same record."""
    from app.engine.precedent import primary_topic

    text = "Costs rose and a supplier concentration issue emerged."

    assert primary_topic(text) == primary_topic(text) is not None
