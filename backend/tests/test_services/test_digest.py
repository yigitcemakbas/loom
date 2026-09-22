"""The digest has one job and one way to ruin it.

Everything here defends the right to interrupt. A digest that arrives when
nothing happened trains a person to delete the next one unread, and a deleted
digest is worse than none: it costs attention, returns nothing, and on the day
something genuinely matters it goes in the bin with the rest.
"""

from datetime import datetime, timedelta, timezone

import pytest

from app.engine.changes import Change
from app.services.digest import (
    FIRST_DIGEST_DAYS,
    MAX_WINDOW_DAYS,
    Digest,
    render,
    window_for,
)

NOW = datetime(2026, 9, 22, 9, 0, tzinfo=timezone.utc)


class _User:
    def __init__(self, frequency="daily", sent_at=None, email="a@b.com"):
        self.digest_frequency = frequency
        self.digest_sent_at = sent_at
        self.email = email
        self.username = "someone"
        self.email_verified_at = NOW


def _change(ticker="AAA", kind="verdict", headline="Something moved.") -> Change:
    return Change(
        ticker=ticker, kind=kind, headline=headline,
        detail="Some detail.", occurred_at=NOW,
    )


# ---- earning the right to interrupt -----------------------------------


def test_a_quiet_day_sends_nothing_at_all():
    """Not a short email, not an "all quiet" note. Silence is the correct
    output and it is what makes an arriving digest mean something."""
    digest = Digest(user=_User(), since=NOW - timedelta(days=1))
    assert digest.total == 0
    assert digest.worth_sending is False


def test_one_real_change_is_worth_sending():
    digest = Digest(user=_User(), since=NOW - timedelta(days=1), held=[_change()])
    assert digest.worth_sending is True


def test_the_subject_names_what_happened_rather_than_the_product():
    """"Loom daily digest" is a subject somebody archives without opening."""
    digest = Digest(
        user=_User(), since=NOW,
        held=[_change(headline="AAA: now leaning negative, was mixed.")],
    )
    subject = digest.subject()
    assert "now leaning negative" in subject
    assert "digest" not in subject.lower()


def test_the_subject_counts_the_rest_rather_than_listing_them():
    digest = Digest(
        user=_User(), since=NOW,
        held=[_change(), _change(ticker="BBB")],
        watched=[_change(ticker="CCC")],
    )
    assert "(and 2 more)" in digest.subject()


# ---- when a digest is due ---------------------------------------------


def test_a_user_who_turned_it_off_is_never_due():
    assert window_for(_User(frequency="off"), NOW) is None


def test_a_first_digest_does_not_reach_back_to_the_beginning_of_time():
    """Otherwise somebody's first morning is a wall of every change since the
    database was created."""
    since, _ = window_for(_User(sent_at=None), NOW)
    assert since == NOW - timedelta(days=FIRST_DIGEST_DAYS)


def test_a_digest_is_not_due_before_its_interval_has_passed():
    recent = _User(frequency="daily", sent_at=NOW - timedelta(hours=3))
    assert window_for(recent, NOW) is None


def test_a_digest_is_due_once_the_interval_has_passed():
    due = _User(frequency="daily", sent_at=NOW - timedelta(days=1, minutes=1))
    assert window_for(due, NOW) is not None


def test_weekly_waits_a_week():
    user = _User(frequency="weekly", sent_at=NOW - timedelta(days=3))
    assert window_for(user, NOW) is None
    user.digest_sent_at = NOW - timedelta(days=8)
    assert window_for(user, NOW) is not None


def test_a_long_gap_is_clamped_rather_than_reported_in_full():
    """A machine off for a month should produce a digest about the last
    fortnight, not a month of history nobody will read."""
    stale = _User(sent_at=NOW - timedelta(days=90))
    since, _ = window_for(stale, NOW)
    assert since == NOW - timedelta(days=MAX_WINDOW_DAYS)


def test_the_window_starts_where_the_last_one_ended():
    """A missed day is caught up on the next run rather than skipped, and
    nothing is ever reported twice."""
    last = NOW - timedelta(days=3)
    since, _ = window_for(_User(sent_at=last), NOW)
    assert since == last


# ---- what a digest says ------------------------------------------------


def test_positions_are_separated_from_things_merely_watched():
    """Money at risk is a different category of attention from curiosity, and
    mixing them buries the first."""
    body = render(Digest(
        user=_User(), since=NOW,
        held=[_change(ticker="HELD")],
        watched=[_change(ticker="WATCH")],
    ))
    assert body.index("YOUR POSITIONS") < body.index("WATCHING")
    assert body.index("HELD") < body.index("WATCH")


def test_a_digest_says_how_to_stop_receiving_it():
    body = render(Digest(user=_User(), since=NOW, held=[_change()]))
    assert "stop these" in body.lower()


def test_a_digest_explains_its_own_silence():
    """So a person does not read a quiet week as Loom being broken."""
    body = render(Digest(user=_User(), since=NOW, held=[_change()]))
    assert "nothing crosses a threshold" in body.lower()


def test_the_body_is_plain_text():
    """Read in three seconds on a phone. Markup adds weight, spam-filter
    surface and a way to render badly, for nothing."""
    body = render(Digest(user=_User(), since=NOW, held=[_change()]))
    assert "<" not in body and "</" not in body


def test_a_change_does_not_name_its_ticker_twice():
    """Feed headlines open with the ticker because the feed shows them without
    one. A digest prints it as its own column, so the two together stutter:
    "AAPL  AAPL: now mixed picture"."""
    body = render(Digest(
        user=_User(), since=NOW,
        held=[_change(ticker="AAPL", headline="AAPL: now mixed picture, was negative.")],
    ))
    assert "AAPL: now mixed" not in body
    assert "now mixed picture" in body
    assert body.count("AAPL") == 1
