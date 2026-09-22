"""Registration, sign-in, email verification, sessions.

A password is what a person signs in with; the emailed code proves, once, that
the address is theirs. Separating the two matters: a code-only sign-in sends
somebody to their mailbox every single visit, and an unverified sign-up lets
anybody claim an address they do not own and block its real owner forever.

Six things here are security properties rather than preferences, and each is
easy to undo by accident later:

**Each secret is stored the way that secret needs.** The password is a scrypt
hash, deliberately expensive because an attacker computes it a billion times.
The code and the session token are SHA-256 digests, which is correct for values
that are already random: stretching them would only slow the defender down.

**Comparison is constant time**, for both. A plain `==` leaks the position of
the first differing byte through timing.

**Neither registering nor signing in reveals whether an account exists.**
Registration answers identically for a fresh address and a taken one, and a
failed sign-in cannot distinguish a wrong password from an unknown user.
Anything else turns these endpoints into a way to enumerate who uses Loom.

**A wrong password costs the same time as an unknown user.** Without a dummy
hash on the miss path, an unknown address returns in microseconds and a known
one takes tens of milliseconds, which is a perfectly usable oracle.

**A code dies on use, on expiry, and on too many wrong guesses.** One in a
million per guess is ample against five attempts and meaningless against
unlimited ones, so the attempt ceiling is the actual protection.

**Issuing a new code invalidates the old one**, or every code a user ever
requested stays live until it expires.
"""

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models.account import LoginCode, Session, User
from app.config import settings
from app.services import passwords, usernames

logger = logging.getLogger(__name__)

# Long enough to arrive and be typed, short enough that an intercepted code is
# usually already dead.
CODE_TTL_MINUTES = 15

# Six digits. Longer is not meaningfully safer once attempts are capped, and it
# is harder to read off a phone.
CODE_DIGITS = 6

# Wrong guesses before the code is burned. Five is generous for a human typing
# from an email and leaves an attacker a one in two hundred thousand chance.
MAX_ATTEMPTS = 5

# Ninety days. This is a tool somebody opens most mornings; a session that
# expires weekly turns the sign-in email into a weekly chore, which is how
# people stop opening a thing at all.
SESSION_TTL_DAYS = 90

# How often a fresh code may be requested for one address. Without this the
# endpoint is a way to send somebody unlimited email.
CODE_COOLDOWN_SECONDS = 30


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalise_email(email: str) -> str:
    """Lowercased and stripped.

    "A@b.com" and "a@b.com " are the same mailbox, and treating them as two
    accounts is a problem that only surfaces once somebody has data in both.
    """
    return email.strip().lower()


def looks_like_email(email: str) -> bool:
    """Deliberately permissive. Real validation is whether the code arrives,
    and a strict pattern here only ever rejects somebody's legitimate address."""
    if len(email) > 254 or email.count("@") != 1:
        return False
    local, _, domain = email.partition("@")
    return bool(local) and "." in domain and not domain.startswith(".") and not domain.endswith(".")


def find_user(db: DbSession, email: str) -> Optional[User]:
    """Look up an account by address.

    Lookup only. This used to create a row when it found nothing, which was
    reasonable while a sign-in was just a code and became wrong the moment
    accounts had passwords and usernames: it left a path that could produce an
    account with neither, which nothing could ever sign in to and nobody could
    re-register.
    """
    return db.execute(
        select(User).where(User.email == normalise_email(email))
    ).scalars().first()


def recently_requested(db: DbSession, user: User) -> bool:
    """Whether a code was issued for this user inside the cooldown."""
    latest = db.execute(
        select(LoginCode)
        .where(LoginCode.user_id == user.id)
        .order_by(LoginCode.created_at.desc())
        .limit(1)
    ).scalars().first()
    if latest is None or latest.created_at is None:
        return False
    created = latest.created_at
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return (_now() - created).total_seconds() < CODE_COOLDOWN_SECONDS


def issue_code(db: DbSession, user: User) -> str:
    """Create a code, invalidating any still outstanding for this user.

    Returns the plaintext, which is the only moment it exists in readable form.
    The caller delivers it and must not store it.
    """
    outstanding = db.execute(
        select(LoginCode)
        .where(LoginCode.user_id == user.id)
        .where(LoginCode.consumed_at.is_(None))
    ).scalars()
    for code in outstanding:
        # Burned rather than deleted, so the attempt history survives for
        # anyone looking at why an account is being hammered.
        code.consumed_at = _now()

    plaintext = f"{secrets.randbelow(10 ** CODE_DIGITS):0{CODE_DIGITS}d}"
    db.add(LoginCode(
        user_id=user.id,
        code_hash=_digest(plaintext),
        expires_at=_now() + timedelta(minutes=CODE_TTL_MINUTES),
    ))
    db.flush()
    return plaintext


class RegistrationError(Exception):
    """Sign-up refused, with a message safe to show a user."""


class SignInError(Exception):
    """Sign-in refused.

    One exception for every cause on purpose. "No such account" and "wrong
    password" are the same message to the caller, because telling them apart
    is how an attacker learns which addresses are worth attacking.
    """


# Hashed once at import and verified against on the miss path, so a sign-in
# attempt for an address that does not exist costs the same as one that does.
# Without it the two differ by tens of milliseconds, which is a usable oracle
# for enumerating accounts.
_DUMMY_HASH = passwords.hash_password("a password nobody will ever use here")


def apply_admin_policy(user: User) -> None:
    """Set the admin flag from configuration.

    Applied on registration and again on every sign-in, so adding a username to
    the list promotes that account on its next visit and removing one demotes
    it. The database therefore never disagrees with the deployment about who
    runs the instance, which is the failure mode of storing the answer only in
    a row somebody can edit.
    """
    user.is_admin = user.username_lower in settings.admin_username_set


def username_taken(db: DbSession, username: str) -> bool:
    folded = usernames.fold(username)
    return db.execute(
        select(User.id).where(User.username_lower == folded)
    ).scalars().first() is not None


def register(db: DbSession, email: str, username: str, password: str) -> tuple[User, bool]:
    """Create an account, or recognise an unfinished one.

    Returns (user, needs_verification). Re-registering an address that exists
    but was never verified is treated as resuming that sign-up rather than as
    an error: the alternative strands somebody who closed the tab before
    entering their code.

    Unlike the address, a taken username IS reported as taken. The two are not
    symmetric: an address is a private contact detail, so confirming one exists
    leaks something about a person, while a username is public by construction
    and the alternative is a sign-up form that rejects every name without
    saying which are free.
    """
    normalised = normalise_email(email)
    if not looks_like_email(normalised):
        raise RegistrationError("That does not look like an email address.")

    name_problem = usernames.problem(username)
    if name_problem:
        raise RegistrationError(name_problem)

    problem = passwords.password_problem(password)
    if problem:
        raise RegistrationError(problem)

    folded = usernames.fold(username)
    display = username.strip()

    existing = db.execute(select(User).where(User.email == normalised)).scalars().first()
    if existing is not None:
        if existing.email_verified_at is not None:
            # Deliberately not "that address is taken". The caller turns this
            # into the same response a fresh sign-up gets, so registration
            # cannot be used to test whether somebody has an account.
            raise RegistrationError("account_exists")
        # Unverified: let the new details stand, since only somebody with
        # access to the mailbox can complete it anyway.
        if folded != existing.username_lower and username_taken(db, folded):
            raise RegistrationError("That username is taken. Pick another.")
        existing.username = display
        existing.username_lower = folded
        existing.password_hash = passwords.hash_password(password)
        apply_admin_policy(existing)
        return existing, True

    if username_taken(db, folded):
        raise RegistrationError("That username is taken. Pick another.")

    user = User(
        email=normalised,
        username=display,
        username_lower=folded,
        password_hash=passwords.hash_password(password),
    )
    apply_admin_policy(user)
    db.add(user)
    db.flush()
    return user, True


def find_by_identifier(db: DbSession, identifier: str) -> User | None:
    """Resolve an email address or a username to an account.

    The presence of an "@" decides which. It is a heuristic and a safe one,
    because usernames cannot contain the character at all.
    """
    value = (identifier or "").strip()
    if not value:
        return None
    if "@" in value:
        return db.execute(
            select(User).where(User.email == normalise_email(value))
        ).scalars().first()
    return db.execute(
        select(User).where(User.username_lower == usernames.fold(value))
    ).scalars().first()


def sign_in(db: DbSession, identifier: str, password: str, *, user_agent: str | None = None) -> str:
    """Check a password against an email address or username, return a token."""
    user = find_by_identifier(db, identifier)

    if user is None or not user.password_hash:
        # Same work as a real check, so the timing does not answer a question
        # the response deliberately refuses to.
        passwords.verify_password(password, _DUMMY_HASH)
        raise SignInError("That email or password is not right.")

    if not passwords.verify_password(password, user.password_hash):
        raise SignInError("That email or password is not right.")

    if user.email_verified_at is None:
        # A distinct signal on purpose. The password was correct, so this tells
        # the owner of the account nothing they do not already know, and
        # without it a half-finished sign-up is an unexplainable dead end.
        raise SignInError("unverified")

    # Silently upgrade a hash made with weaker parameters, so the cost can be
    # raised over time without a migration or a forced reset.
    if passwords.needs_rehash(user.password_hash):
        user.password_hash = passwords.hash_password(password)

    # Reapplied here, not only at registration. The deployment decides who
    # administers the instance, so adding a username to the list promotes that
    # account on its next visit and removing one demotes it. A row somebody
    # edited can never disagree with the configuration for longer than one
    # sign-in.
    apply_admin_policy(user)
    user.last_seen_at = _now()
    return _create_session(db, user, user_agent=user_agent)


class VerificationError(Exception):
    """Verification failed. The message is safe to show a user.

    Deliberately one exception type for every failure mode. Distinguishing
    "wrong code" from "no code outstanding" tells an attacker which addresses
    have a live code, and the user's next action is the same either way:
    request a new one.
    """


def verify_code(db: DbSession, email: str, submitted: str, *, user_agent: str | None = None) -> str:
    """Confirm an address with its code and return a session token.

    Marks the account verified, which is the thing the code exists to do. The
    token is returned once and stored only as a digest, so a database dump
    yields nothing replayable.
    """
    normalised = normalise_email(email)
    user = db.execute(select(User).where(User.email == normalised)).scalars().first()
    if user is None:
        raise VerificationError("That code is not valid. Request a new one.")

    code = db.execute(
        select(LoginCode)
        .where(LoginCode.user_id == user.id)
        .where(LoginCode.consumed_at.is_(None))
        .order_by(LoginCode.created_at.desc())
        .limit(1)
    ).scalars().first()

    if code is None:
        raise VerificationError("That code is not valid. Request a new one.")

    expires = code.expires_at if code.expires_at.tzinfo else code.expires_at.replace(tzinfo=timezone.utc)
    if expires < _now():
        raise VerificationError("That code has expired. Request a new one.")

    if code.attempts >= MAX_ATTEMPTS:
        code.consumed_at = _now()
        db.flush()
        raise VerificationError("Too many attempts. Request a new code.")

    code.attempts += 1
    # Constant time: a plain == leaks the position of the first differing byte.
    if not hmac.compare_digest(code.code_hash, _digest(submitted.strip())):
        db.flush()
        raise VerificationError("That code is not valid. Request a new one.")

    code.consumed_at = _now()
    if user.email_verified_at is None:
        user.email_verified_at = _now()
    apply_admin_policy(user)
    user.last_seen_at = _now()
    return _create_session(db, user, user_agent=user_agent)


def _create_session(db: DbSession, user: User, *, user_agent: str | None) -> str:
    token = secrets.token_urlsafe(32)
    db.add(Session(
        user_id=user.id,
        token_hash=_digest(token),
        expires_at=_now() + timedelta(days=SESSION_TTL_DAYS),
        last_used_at=_now(),
        user_agent=(user_agent or "")[:400] or None,
    ))
    db.flush()
    return token


def user_for_token(db: DbSession, token: str) -> Optional[User]:
    """Resolve a session token, or None. Touches `last_used_at` on success."""
    if not token:
        return None
    session = db.execute(
        select(Session).where(Session.token_hash == _digest(token))
    ).scalars().first()
    if session is None:
        return None

    expires = session.expires_at if session.expires_at.tzinfo else session.expires_at.replace(tzinfo=timezone.utc)
    if expires < _now():
        # Expired sessions are removed on encounter rather than by a sweeper,
        # which keeps the table tidy without another scheduled job.
        db.delete(session)
        db.flush()
        return None

    session.last_used_at = _now()
    user = db.get(User, session.user_id)
    if user is not None:
        user.last_seen_at = _now()
    return user


def revoke_token(db: DbSession, token: str) -> bool:
    session = db.execute(
        select(Session).where(Session.token_hash == _digest(token))
    ).scalars().first()
    if session is None:
        return False
    db.delete(session)
    return True


__all__ = [
    "CODE_COOLDOWN_SECONDS",
    "RegistrationError",
    "apply_admin_policy",
    "find_by_identifier",
    "username_taken",
    "SignInError",
    "register",
    "sign_in",
    "CODE_DIGITS",
    "CODE_TTL_MINUTES",
    "MAX_ATTEMPTS",
    "SESSION_TTL_DAYS",
    "VerificationError",
    "find_user",
    "issue_code",
    "looks_like_email",
    "normalise_email",
    "recently_requested",
    "revoke_token",
    "user_for_token",
    "verify_code",
]
