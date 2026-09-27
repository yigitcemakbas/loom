"""Evidence that is older than it looks.

Every date Loom stores is correct, which is why this needed a module of its own.
A finding carries the date of the document it was extracted from, so a reader
checking whether a verdict is current sees a recent timestamp and stops. A
filing published this month can quote a call from two years ago, and nothing in
the record distinguished the two.

The bar this has to clear is not detection, it is *rarity*. A warning that
appears on every annual report is read on none of them, and a financial document
naming last year is the ordinary register of the form rather than a defect.
"""

import uuid
from datetime import datetime, timedelta, timezone

from app.engine.staleness import (
    SIDE_AGE_GAP_DAYS,
    STALE_YEARS_BEHIND,
    assess,
    effective_age_days,
    quoted_years,
    side_ages,
    stale_ids,
)

NOW = datetime(2026, 9, 26, tzinfo=timezone.utc)


class _Finding:
    def __init__(self, *, quote=None, days_ago=10, direction="negative"):
        self.id = uuid.uuid4()
        self.evidence_quote = quote
        self.occurred_at = NOW - timedelta(days=days_ago)
        self.market_direction = direction


# ---- reading a period out of a quote -----------------------------------


def test_four_digit_years_are_read_as_periods():
    assert quoted_years("Greater China sales fell during 2025 compared to 2024") == {2024, 2025}


def test_numbers_that_are_not_years_are_not_read_as_years():
    """A dollar figure, a unit count and a patent number all look like years to
    a loose pattern, and each false hit spends the credibility of a real one."""
    assert quoted_years("a $100 billion programme across 2500 stores") == set()
    assert quoted_years("no periods mentioned at all") == set()


def test_no_quote_is_not_a_stale_quote():
    """Absence of evidence about the period is not evidence the period is old.
    Findings derived from filed facts carry no quote at all."""
    assert assess(_Finding(quote=None)).is_stale is False


# ---- the rarity bar ----------------------------------------------------


def test_a_filing_discussing_last_year_is_not_stale():
    """The case that decides whether this feature is usable. An annual report
    filed in February covers the year that just closed and says so throughout;
    a quarterly report compares against the same quarter a year earlier. If
    naming last year counted as stale, every filing Loom holds would be
    flagged and the warning would mean nothing."""
    annual_report = _Finding(quote="Net sales during fiscal 2025 decreased from 2024.", days_ago=200)

    assert assess(annual_report).is_stale is False


def test_a_quote_about_a_period_before_last_year_is_stale():
    """Measured on the stored corpus this fires on 8 findings out of 428, and
    every one is genuine: a 2012 tax notice, a 2022 subpoena, a 2024 assessment,
    each carried forward into a filing published years later."""
    old_notice = _Finding(
        quote="The Notices seek to increase taxable income for the years 2010 to 2012.",
        days_ago=20,
    )

    result = assess(old_notice)
    assert result.is_stale
    assert result.years_behind >= STALE_YEARS_BEHIND
    assert "2012" in result.note


def test_the_note_says_what_is_wrong_rather_than_that_something_is():
    """A warning a reader cannot act on is decoration. Naming the period is
    what lets them decide whether it still matters."""
    result = assess(_Finding(quote="Since 2022 we have received multiple subpoenas.", days_ago=5))

    assert result.note is not None
    assert "2022" in result.note


def test_stale_ids_selects_only_the_stale():
    current = _Finding(quote="Results for 2026 improved.", days_ago=5)
    old = _Finding(quote="A 2021 investigation remains open.", days_ago=5)

    found = stale_ids([current, old])

    assert str(old.id) in found
    assert str(current.id) not in found


# ---- age measured by content, not by timestamp -------------------------


def test_effective_age_counts_the_period_the_quote_describes():
    """The distinction the module turns on. Under a document-date definition
    the medians on both sides of every company in the corpus were identical,
    because findings inherit the date of the filing they came from and both
    sides usually come from the same filing. The check could not fire."""
    restated = _Finding(quote="A 2022 investigation remains unresolved.", days_ago=10)

    by_document = 10
    assert effective_age_days(restated, now=NOW) > by_document


def test_a_recent_quote_is_not_aged_by_its_own_year():
    """The end of the named year, not its start. Taking the start would add up
    to a year of staleness to every ordinary filing."""
    fresh = _Finding(quote="Revenue in 2026 rose sharply.", days_ago=3)

    assert effective_age_days(fresh, now=NOW) == 3


# ---- the two sides of a case -------------------------------------------


def test_a_case_whose_sides_are_the_same_age_reports_nothing():
    same = [
        _Finding(direction="positive", days_ago=30),
        _Finding(direction="positive", days_ago=32),
        _Finding(direction="negative", days_ago=31),
        _Finding(direction="negative", days_ago=29),
    ]

    assert side_ages(same, now=NOW).note is None


def test_a_case_built_on_old_positives_and_current_concerns_says_so():
    """The failure this exists for. A reader weighing current concerns against
    year-old encouragement is comparing across time, and each point on the page
    shows a plausible date."""
    lopsided = [
        _Finding(direction="positive", days_ago=SIDE_AGE_GAP_DAYS + 90),
        _Finding(direction="positive", days_ago=SIDE_AGE_GAP_DAYS + 100),
        _Finding(direction="negative", days_ago=20),
        _Finding(direction="negative", days_ago=25),
    ]

    result = side_ages(lopsided, now=NOW)

    assert result.is_imbalanced
    assert result.older_side == "positive"
    # Names which side is old, because that is what a reader discounts. A gap
    # in days tells them a number and leaves the work undone.
    assert "encouraging evidence" in result.note


def test_one_finding_a_side_is_not_a_comparison():
    """A median of one observation is that observation, and calling two
    findings a stale comparison because one is older is noise."""
    thin = [
        _Finding(direction="positive", days_ago=700),
        _Finding(direction="negative", days_ago=5),
    ]

    assert side_ages(thin, now=NOW).note is None
