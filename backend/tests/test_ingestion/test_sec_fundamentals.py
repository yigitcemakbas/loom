"""Tests for SEC XBRL fundamentals.

Two hazards dominate and both produce plausible-looking wrong numbers rather
than errors: reading a year-to-date figure as a quarter, and dating a fact by
the period it covers rather than the day it was published.
"""

from datetime import datetime, timezone

import pytest

from app.ingestion.facts.sec_fundamentals import (
    ACCEPTED_FORMS,
    CONCEPTS,
    SecFundamentalsAdapter,
)


def _payload(**obs):
    base = dict(
        start="2026-03-29", end="2026-06-27", val=109_400_000_000,
        filed="2026-07-31", form="10-Q", fy=2026, fp="Q3", accn="x",
    )
    base.update(obs)
    return {"facts": {"us-gaap": {"Revenues": {"units": {"USD": [base]}}}}}


def _facts(payload, since=None):
    return list(SecFundamentalsAdapter()._to_facts("AAPL", payload, since))


# ---- point-in-time ---------------------------------------------------------


def test_fact_is_dated_by_when_it_was_published_not_the_period_it_covers():
    """A quarter's revenue dated to the quarter's end appears knowable weeks
    before anyone could have known it, which is the lookahead that makes a
    backtest look brilliant and a live strategy lose."""
    fact = _facts(_payload())[0]
    assert fact.as_of_date.date() == datetime(2026, 7, 31).date()
    assert fact.attributes["period_end"] == "2026-06-27"


def test_period_end_is_preserved_alongside_the_filed_date():
    fact = _facts(_payload())[0]
    assert fact.attributes["period_start"] == "2026-03-29"
    assert fact.attributes["fiscal_period"] == "Q3"


# ---- period classification -------------------------------------------------


@pytest.mark.parametrize(
    "start,end,expected",
    [
        ("2026-03-29", "2026-06-27", "quarter"),       # 90 days
        ("2025-12-29", "2026-06-27", "half_year"),     # ~180
        ("2025-09-29", "2026-06-27", "nine_months"),   # ~271
        ("2025-06-29", "2026-06-27", "year"),          # ~363
    ],
)
def test_periods_are_classified_so_a_quarter_is_never_read_as_a_year(start, end, expected):
    """Apple's newest reported 'revenue' spans nine months, not three. Comparing
    one company's quarter against another's year-to-date is silently wrong and
    looks entirely plausible."""
    fact = _facts(_payload(start=start, end=end))[0]
    assert fact.attributes["period"] == expected


def test_balance_sheet_items_are_marked_as_instants_not_durations():
    """Assets and cash are a position at a moment, not a flow over a period.
    Forcing them into a duration would invent a span that does not exist."""
    fact = _facts(_payload(start=None))[0]
    assert fact.attributes["period"] == "instant"
    assert fact.attributes["period_days"] == 0


# ---- what is accepted ------------------------------------------------------


def test_only_audited_periodic_reports_are_stored():
    """XBRL also carries 8-K and S-1 facts, which duplicate or pre-date the
    audited figures."""
    assert _facts(_payload(form="8-K")) == []
    assert "10-Q" in ACCEPTED_FORMS and "10-K" in ACCEPTED_FORMS


def test_facts_without_a_value_or_filing_date_are_skipped():
    assert _facts(_payload(val=None)) == []
    assert _facts(_payload(filed=None)) == []


def test_since_filters_on_the_filing_date():
    after = datetime(2026, 8, 1, tzinfo=timezone.utc)
    assert _facts(_payload(), since=after) == []
    before = datetime(2026, 1, 1, tzinfo=timezone.utc)
    assert len(_facts(_payload(), since=before)) == 1


def test_concept_aliases_exist_for_metrics_that_were_renamed():
    """Apple reports revenue under RevenueFromContractWithCustomerExcludingAssessedTax
    recently and Revenues historically. Taking only the first name found would
    produce a series with a hole in the middle."""
    assert len(CONCEPTS["revenue"]) > 1
    assert "Revenues" in CONCEPTS["revenue"]


def test_unknown_concepts_are_ignored_rather_than_guessed():
    payload = {"facts": {"us-gaap": {"SomeConceptWeDoNotTrack": {"units": {"USD": []}}}}}
    assert _facts(payload) == []
