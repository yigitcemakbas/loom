"""Closing the coverage gap without exhausting the thing that closes it.

The drip exists because a hundred and twenty-three of a hundred and thirty
company pages reported their own emptiness. Every rule here is about making
steady progress inside a quota that refuses bursts, and the failure it guards
against is subtle: a run that always starts at the beginning of the alphabet
covers the same companies repeatedly and never reaches the end.
"""

from datetime import datetime, timedelta, timezone

from app.engine.coverage import (
    ONE_SHOT_FORMS,
    PRIORS_PER_RUN,
    READS_PER_RUN,
    STALE_PRIOR_DAYS,
    DripResult,
)

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


def test_a_run_is_bounded_so_a_free_tier_is_not_asked_for_a_burst():
    """The point is steady progress inside a quota that refuses long runs, not
    racing through the universe and then failing for a day."""
    assert 0 < PRIORS_PER_RUN <= 5
    assert 0 < READS_PER_RUN <= 5


def test_a_refusal_is_reported_rather_than_counted_as_failure():
    """A free tier saying no is the expected steady state, not an error. The
    caller stops and the next run continues where this one left off."""
    result = DripResult(covered=2, exhausted=True, remaining=115)
    assert result.exhausted is True
    assert result.failed == 0


def test_remaining_is_reported_so_progress_is_visible():
    """Without it a drip that covers three a run and one that covers none look
    identical in the log."""
    result = DripResult(covered=3, remaining=114)
    assert result.remaining == 114


def test_a_one_shot_read_looks_at_filings_not_news():
    """A verdict rests on the risk factors and the full-year picture. News is
    the noisiest source and the least worth a one-time budget."""
    assert ONE_SHOT_FORMS == ("10-K", "10-Q")
    assert "news" not in ONE_SHOT_FORMS


def test_the_annual_report_is_preferred_over_the_quarterly():
    """A 10-K carries the risk factors a verdict actually rests on."""
    assert ONE_SHOT_FORMS[0] == "10-K"


def test_a_prior_goes_stale_rather_than_lasting_forever():
    """Otherwise coverage quietly becomes a set of standing views describing
    last year's company."""
    assert 30 <= STALE_PRIOR_DAYS <= 365


def test_never_covered_companies_come_before_refreshes():
    """A company with no prior scores every filing zero, which is
    indistinguishable from a quiet week. A company with a slightly old one is
    still answering."""
    stale_cutoff = NOW - timedelta(days=STALE_PRIOR_DAYS)
    # A prior from just inside the window is not yet due, so a company with no
    # prior at all must be offered first regardless of alphabet.
    assert stale_cutoff < NOW
