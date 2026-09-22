"""Accounts, verification codes, sessions, and what a user actually owns.

Four tables, and the shape of each is driven by one idea: Loom cannot route a
person's attention until it knows what they care about, and it cannot know that
without a durable identity to hang it on. Everything before this treated all
hundred and thirty companies as equally interesting to everybody, which is the
same as treating none of them as interesting.

**A password and a verified address, not one or the other.** The password is
what a person signs in with; the emailed code exists to prove the address is
theirs before the account can be used. Without the code, anybody can claim any
address and sit in the way of its real owner forever.

**Nothing secret is stored in the clear, and each secret is stored the right
way.** The password is a scrypt hash, which is deliberately expensive to
compute because an attacker will compute it a billion times. The verification
code and the session token are SHA-256 digests, which is correct for values
that are already random and high-entropy: stretching them would only slow down
the defender. Using one treatment for both jobs is the usual mistake.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class User(Base):
    """One person. Identified only by an email address."""

    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # Stored lowercased and stripped, because "A@b.com" and "a@b.com " are the
    # same mailbox and treating them as two accounts is a support problem that
    # only ever appears after it is too late to fix cheaply.
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)

    # Two columns for one name, because a username is two things at once. The
    # display form keeps the capitalisation the person chose; the folded form
    # is what uniqueness and lookup use, so "Yigit" and "yigit" cannot both
    # exist and either spelling signs the same account in.
    username: Mapped[str] = mapped_column(String, nullable=False)
    username_lower: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)

    display_name: Mapped[str | None] = mapped_column(String, nullable=True)

    # Oversight, not capability. Running the engine is what Loom is for and
    # every account can do it; this grants sight of other accounts and of the
    # engine's internals, which are the two things a person should not be able
    # to see by default on an instance somebody else also uses.
    is_admin: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # scrypt, self-describing (see services/passwords.py). Nullable only so a
    # half-finished sign-up can be resumed; an account cannot sign in without
    # one.
    password_hash: Mapped[str | None] = mapped_column(String, nullable=True)

    # Set when the emailed code is entered. Until then the account exists and
    # cannot sign in, which is what stops somebody claiming an address that is
    # not theirs and then being stuck in the way of the real owner.
    email_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # How often Loom may interrupt this person, and when it last did.
    #
    # "daily" is the default rather than "off", because a research tool nobody
    # is told to open is a research tool nobody opens. It is a low bar to
    # interrupt somebody and a lower one to stop, and the digest sends nothing
    # at all on a day when nothing crossed a threshold, which is what keeps the
    # default defensible.
    digest_frequency: Mapped[str] = mapped_column(String, nullable=False, default="daily")
    # The moment covered up to. The next digest reports changes since this, so
    # a missed day is caught up rather than skipped, and nothing is ever sent
    # twice.
    digest_sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class LoginCode(Base):
    """A one-time sign-in code, stored as a digest.

    Carries its own attempt counter rather than relying on a global rate limit.
    A six digit code is one in a million per guess, which is ample against a
    handful of attempts and trivial against unlimited ones, so the ceiling is
    the security property and not a nicety.
    """

    __tablename__ = "login_codes"
    __table_args__ = (Index("ix_login_codes_user_expires", "user_id", "expires_at"),)

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String, nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Set the moment a code is used, so a code cannot be replayed even inside
    # its own lifetime.
    consumed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Session(Base):
    """A signed-in browser, stored as a digest of its token.

    Long lived on purpose. This is a research tool somebody opens most
    mornings, and a session that expires weekly turns the sign-in email into a
    weekly chore, which is how people end up leaving tools open or not opening
    them at all.
    """

    __tablename__ = "user_sessions"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    token_hash: Mapped[str] = mapped_column(String, nullable=False, unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Free-text, for a sessions list a user can recognise their own devices in.
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)


class Position(Base):
    """What a user actually owns, and at what cost.

    This is the table that changes what Loom is. Without it Loom can describe a
    company; with it Loom can say "the case for something you hold has
    weakened", which is the only sentence in this product that is worth an
    interruption.

    `shares` and `cost_basis` are Numeric rather than float: a position is
    money, and binary floating point on money produces totals that do not match
    the user's own arithmetic, which destroys trust in everything beside it.
    """

    __tablename__ = "positions"
    __table_args__ = (
        UniqueConstraint("user_id", "company_id", name="uq_positions_user_company"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # Null for a company the user is only watching. A watch and a holding are
    # the same object here because the difference is exactly one number, and
    # splitting them into two tables would mean two lists to keep in step.
    shares: Mapped[float | None] = mapped_column(Numeric(20, 6), nullable=True)
    cost_basis: Mapped[float | None] = mapped_column(Numeric(20, 4), nullable=True)
    opened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    @property
    def is_held(self) -> bool:
        """A watch becomes a holding the moment it has a size."""
        return self.shares is not None and float(self.shares) > 0
