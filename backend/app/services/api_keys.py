"""Issuing, checking and revoking programmatic credentials.

Mirrors `services/auth.py` deliberately: the same digest, the same constant-time
comparison, the same rule that the database never holds a usable secret. A
second credential type invented its own crypto conventions would be the obvious
way for one of them to be weaker than the other.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import uuid as _uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.account import User
from app.models.api_key import ApiKey

logger = logging.getLogger(__name__)

# Names the credential and the instance that issued it, so a key found loose in
# a config file or a log is identifiable without a lookup. Deliberately not a
# bare random string: an unlabelled secret is one nobody can trace.
PREFIX = "loom_sk_"

# Bytes of entropy. Matches the session token, which is the same kind of secret
# with the same exposure.
TOKEN_BYTES = 32

# How much of the plaintext is kept in clear for identification. Eight
# characters of a 43-character token distinguishes keys in a list while leaving
# far too little to guess the rest.
VISIBLE_CHARS = 8

MAX_KEYS_PER_USER = 10


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def issue(db: Session, user: User, name: str) -> tuple[ApiKey, str]:
    """Create a key. Returns the row and the plaintext, which is shown once.

    The caller is responsible for putting the plaintext in front of the owner
    immediately, because nothing can recover it afterwards. That is the point:
    a key the server can read back is a key an attacker who reaches the database
    can read back.
    """
    label = (name or "").strip() or "unnamed key"

    live = db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id).where(ApiKey.revoked_at.is_(None))
    ).scalars().all()
    if len(live) >= MAX_KEYS_PER_USER:
        raise ValueError(
            f"That account already has {MAX_KEYS_PER_USER} active keys. "
            f"Revoke one before issuing another."
        )

    plaintext = PREFIX + secrets.token_urlsafe(TOKEN_BYTES)
    key = ApiKey(
        user_id=user.id,
        name=label[:80],
        prefix=plaintext[: len(PREFIX) + VISIBLE_CHARS],
        key_hash=_digest(plaintext),
    )
    db.add(key)
    db.commit()
    db.refresh(key)
    logger.info("Issued API key %s for user %s.", key.prefix, user.username)
    return key, plaintext


def user_for_key(db: Session, presented: str) -> Optional[User]:
    """The account a key belongs to, or None.

    Looked up by digest rather than scanned, so the cost does not grow with the
    number of keys issued. The constant-time comparison afterwards is belt and
    braces on an already-exact index match, and costs nothing.
    """
    token = (presented or "").strip()
    if not token.startswith(PREFIX):
        return None

    key = db.execute(
        select(ApiKey).where(ApiKey.key_hash == _digest(token))
    ).scalars().first()
    if key is None or not key.active:
        return None
    if not hmac.compare_digest(key.key_hash, _digest(token)):
        return None

    # Written on every use so an owner can tell a live key from a forgotten one.
    # Committed separately from whatever the request goes on to do, because a
    # failed request should still record that the credential was used.
    key.last_used_at = datetime.now(timezone.utc)
    db.commit()

    return db.execute(select(User).where(User.id == key.user_id)).scalars().first()


def list_for(db: Session, user: User) -> list[ApiKey]:
    """Every key the account has, revoked ones included.

    Revoked keys stay listed because the question an owner actually has is "what
    happened to the key I issued in March", and a list that forgets cannot
    answer it.
    """
    return list(db.execute(
        select(ApiKey).where(ApiKey.user_id == user.id).order_by(ApiKey.created_at.desc())
    ).scalars().all())


def revoke(db: Session, user: User, key_id: str) -> bool:
    """Revoke one of this account's keys. Returns False when there is no match.

    Scoped to the owner in the query rather than checked afterwards, so a key id
    belonging to somebody else is indistinguishable from one that does not
    exist. Reporting the difference would let anyone with an account probe for
    valid key ids.
    """
    # Coerced here rather than at the route, and a malformed id is a miss
    # rather than an error: the id arrives from a URL, so "not a UUID" is
    # ordinary bad input and a 500 would be the wrong answer to it.
    try:
        identifier = _uuid.UUID(str(key_id))
    except (ValueError, AttributeError, TypeError):
        return False

    key = db.execute(
        select(ApiKey).where(ApiKey.id == identifier).where(ApiKey.user_id == user.id)
    ).scalars().first()
    if key is None:
        return False
    if key.revoked_at is None:
        key.revoked_at = datetime.now(timezone.utc)
        db.commit()
        logger.info("Revoked API key %s.", key.prefix)
    return True


__all__ = ["MAX_KEYS_PER_USER", "PREFIX", "issue", "list_for", "revoke", "user_for_key"]
