"""Reaching somebody who is not looking at Loom.

The change feed answers "what moved since I last looked" and still requires
remembering to look, which is the failure it was built to fix, moved one level
up. This is the part that closes it.

The whole design is about earning the right to interrupt, because a digest that
arrives when nothing happened trains a person to delete it unread, and a
deleted digest is worse than none: it costs attention and returns nothing, and
the day something genuinely matters it goes in the bin with the rest.

Four rules follow from that:

**Nothing is sent when nothing crossed a threshold.** Not a short email, not an
"all quiet" note. Silence is the correct output for a quiet day and it is what
makes an arriving digest mean something.

**Only companies this person follows.** Loom knows about a hundred and thirty
and a person cares about a handful. A change at a company they have never heard
of is not news to them, however large.

**Held positions outrank watched ones.** Money at risk is a different category
of attention from curiosity, and a digest that mixes them buries the first.

**Windowed from the last digest, not from a fixed period.** A day the machine
was asleep is caught up on the next run rather than silently skipped, and a
change is never reported twice.
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.engine.changes import Change, recent_changes
from app.models.account import Position, User
from app.models.company import Company
from app.services.mailer import send_digest

logger = logging.getLogger(__name__)

# How long a digest may look back when a user has never had one. Without a
# ceiling, a first digest would report every change since the database was
# created, which is an unreadable wall on somebody's first morning.
FIRST_DIGEST_DAYS = 7

# The most a digest may cover however long the gap. A machine off for a month
# should produce a digest about the last few days, not a month of history
# nobody will read.
MAX_WINDOW_DAYS = 14

# Kinds ordered by how much they justify an interruption, matching the feed.
_SEVERITY = {"verdict": 3, "factor": 2, "event": 1}

FREQUENCIES = {
    "off": None,
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
}


@dataclass
class Digest:
    """What one person would be sent, if anything."""

    user: User
    since: datetime
    held: list[Change] = field(default_factory=list)
    watched: list[Change] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.held) + len(self.watched)

    @property
    def worth_sending(self) -> bool:
        """Silence is the correct output for a quiet day.

        An "all quiet" email trains a person to delete the next one unread,
        which costs the digest its whole value on the day it matters.
        """
        return self.total > 0

    def subject(self) -> str:
        """Specific enough to act on from a lock screen.

        "Loom daily digest" is a subject somebody archives without opening.
        Naming the company and what happened to it is the difference between a
        notification and a newsletter.
        """
        lead = (self.held or self.watched)[0]
        others = self.total - 1
        # The headline already opens with the ticker, so it is not repeated.
        if others == 0:
            return f"Loom: {lead.headline}"
        return f"Loom: {lead.headline} (and {others} more)"


def window_for(user: User, now: datetime) -> Optional[tuple[datetime, datetime]]:
    """The period a digest should cover, or None if it is not due yet."""
    interval = FREQUENCIES.get(user.digest_frequency or "off")
    if interval is None:
        return None

    last = user.digest_sent_at
    if last is None:
        return now - timedelta(days=FIRST_DIGEST_DAYS), now
    if last.tzinfo is None:
        last = last.replace(tzinfo=timezone.utc)

    if now - last < interval:
        return None
    # Clamped, so a machine that was off for a month produces a digest about
    # the last fortnight rather than a month of history nobody will read.
    return max(last, now - timedelta(days=MAX_WINDOW_DAYS)), now


def build_digest(db: DbSession, user: User, *, now: Optional[datetime] = None) -> Optional[Digest]:
    """What this person would be sent, or None when it is not due."""
    now = now or datetime.now(timezone.utc)
    window = window_for(user, now)
    if window is None:
        return None
    since, _ = window

    rows = db.execute(
        select(Company.ticker, Position.shares)
        .join(Position, Position.company_id == Company.id)
        .where(Position.user_id == user.id)
    ).all()
    if not rows:
        # Nothing followed, so nothing is news to this person yet.
        return Digest(user=user, since=since)

    held_tickers = {t for t, shares in rows if shares is not None and float(shares) > 0}
    followed = {t for t, _ in rows}

    days = max(1, (now - since).days + 1)
    changes = [c for c in recent_changes(db, days=days, limit=200) if c.ticker in followed]
    # recent_changes rounds to whole days, so the tail can reach back before
    # the window; filtered here rather than there because the feed's own
    # callers want the looser behaviour.
    changes = [c for c in changes if _aware(c.occurred_at) >= since]
    changes.sort(key=lambda c: (_SEVERITY.get(c.kind, 0), c.occurred_at), reverse=True)

    return Digest(
        user=user,
        since=since,
        held=[c for c in changes if c.ticker in held_tickers],
        watched=[c for c in changes if c.ticker not in held_tickers],
    )


def _line(change: Change) -> list[str]:
    """One change, without saying its ticker twice.

    Change headlines open with the ticker because the feed shows them without
    one, and a digest prints it as its own column, so the two together read as
    a stutter: "AAPL  AAPL: now mixed picture".
    """
    prefix = f"{change.ticker}: "
    headline = change.headline
    if headline.startswith(prefix):
        headline = headline[len(prefix):]
    lines = [f"  {change.ticker:<6} {headline}"]
    if change.detail:
        lines.append(f"         {change.detail}")
    lines.append("")
    return lines


def render(digest: Digest) -> str:
    """Plain text. No HTML, no images, no tracking pixel.

    A digest is read in three seconds on a phone and its entire job is to say
    whether opening Loom is worth it. Markup would add weight, spam-filter
    surface and a way for the message to render badly, for nothing.
    """
    lines: list[str] = []

    if digest.held:
        lines.append("YOUR POSITIONS")
        lines.append("")
        for change in digest.held:
            lines.extend(_line(change))

    if digest.watched:
        lines.append("WATCHING")
        lines.append("")
        for change in digest.watched:
            lines.extend(_line(change))

    lines.append("-" * 58)
    lines.append(
        f"Covering {digest.since:%d %b %H:%M} UTC onwards. Loom sends nothing on a "
        "day when nothing crosses a threshold."
    )
    lines.append("Change how often, or stop these, in Loom under Portfolio.")
    return "\n".join(lines)


def send_due_digests(db: DbSession, *, now: Optional[datetime] = None) -> int:
    """Send to everybody due one. Returns how many went out."""
    now = now or datetime.now(timezone.utc)
    sent = 0

    for user in db.execute(select(User)).scalars():
        if (user.digest_frequency or "off") == "off":
            continue
        if user.email_verified_at is None:
            # Never send to an address nobody has proved they own.
            continue

        try:
            digest = build_digest(db, user, now=now)
        except Exception:
            logger.exception("Could not build a digest for %s", user.username)
            continue
        if digest is None:
            continue

        if not digest.worth_sending:
            # The window still advances. Otherwise a quiet week would make the
            # next digest reach back over all of it and arrive as a wall.
            user.digest_sent_at = now
            continue

        if send_digest(user.email, digest.subject(), render(digest)):
            user.digest_sent_at = now
            sent += 1
        else:
            # Left alone on failure, so the next run retries the same window
            # rather than skipping past whatever could not be delivered.
            logger.warning("Digest delivery failed for %s; will retry.", user.username)

    db.commit()
    return sent


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


__all__ = [
    "FIRST_DIGEST_DAYS",
    "FREQUENCIES",
    "MAX_WINDOW_DAYS",
    "Digest",
    "build_digest",
    "render",
    "send_due_digests",
    "window_for",
]
