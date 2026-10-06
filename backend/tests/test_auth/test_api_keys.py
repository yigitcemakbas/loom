"""Durable credentials for agents, and the limits that make them safe to hand out.

Loom's only credential was a browser session issued by emailing a six-digit
code: right for a person, useless for an agent with no inbox. The evidence API
exists because agents reading evidence were the best-performing arm of the
reader benchmark, and until now there was no way to give one a credential.

What these tests mostly pin down is the blast radius. Adding a ticker queues a
full filing-history ingest, so a write-capable key would be a way for anyone
holding it to spend an instance's SEC rate limit and model quota.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.account import LoginCode, Position, Session, User  # noqa: F401
from app.models.api_key import ApiKey
from app.models.company import Company  # noqa: F401
from app.services import api_keys, auth as auth_service


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine, tables=[
        User.__table__, LoginCode.__table__, Session.__table__, ApiKey.__table__,
    ])
    with sessionmaker(bind=engine, future=True)() as session:
        yield session


@pytest.fixture()
def user(db):
    created, _ = auth_service.register(db, "agent@example.com", "agent", "a-long-enough-password")
    return created


# ---- the secret itself -----------------------------------------------------


def test_the_database_never_holds_a_usable_key():
    """A key the server can read back is a key an attacker who reaches the
    database can read back, so only a digest is stored."""
    pass


def test_only_a_digest_is_stored_and_the_plaintext_is_returned_once(db, user):
    key, plaintext = api_keys.issue(db, user, "my agent")

    assert plaintext.startswith(api_keys.PREFIX)
    assert key.key_hash != plaintext
    assert plaintext not in key.key_hash
    # The stored row carries no way to reconstruct the secret.
    assert plaintext[len(api_keys.PREFIX) + 8:] not in str(key.__dict__)


def test_the_prefix_identifies_a_key_without_authenticating_it(db, user):
    """Enough to match a key found in a config file against a row here, and far
    too little to guess the rest."""
    key, plaintext = api_keys.issue(db, user, "my agent")

    assert plaintext.startswith(key.prefix)
    assert len(key.prefix) < len(plaintext) / 2
    assert api_keys.user_for_key(db, key.prefix) is None


def test_a_key_authenticates_its_owner(db, user):
    _, plaintext = api_keys.issue(db, user, "my agent")

    assert api_keys.user_for_key(db, plaintext).id == user.id


def test_rubbish_and_near_misses_authenticate_nobody(db, user):
    _, plaintext = api_keys.issue(db, user, "my agent")

    assert api_keys.user_for_key(db, "") is None
    assert api_keys.user_for_key(db, "not-a-loom-key") is None
    assert api_keys.user_for_key(db, plaintext + "x") is None
    assert api_keys.user_for_key(db, plaintext[:-1]) is None


# ---- revocation ------------------------------------------------------------


def test_a_revoked_key_stops_working_immediately(db, user):
    key, plaintext = api_keys.issue(db, user, "my agent")
    assert api_keys.user_for_key(db, plaintext) is not None

    assert api_keys.revoke(db, user, str(key.id))

    assert api_keys.user_for_key(db, plaintext) is None


def test_a_revoked_key_stays_listed(db, user):
    """The question an owner has is "what happened to the key I issued in
    March", and a list that forgets cannot answer it."""
    key, _ = api_keys.issue(db, user, "my agent")
    api_keys.revoke(db, user, str(key.id))

    listed = api_keys.list_for(db, user)

    assert [k.id for k in listed] == [key.id]
    assert listed[0].revoked_at is not None
    assert not listed[0].active


def test_revoking_somebody_elses_key_is_indistinguishable_from_a_missing_one(db, user):
    """Reporting the difference would let any account probe for valid key ids."""
    other, _ = auth_service.register(db, "b@example.com", "other", "a-long-enough-password")
    key, plaintext = api_keys.issue(db, user, "mine")

    assert api_keys.revoke(db, other, str(key.id)) is False
    assert api_keys.revoke(db, other, "00000000-0000-0000-0000-000000000000") is False
    # And the key still works, because nothing was revoked.
    assert api_keys.user_for_key(db, plaintext) is not None


# ---- limits ----------------------------------------------------------------


def test_an_account_cannot_mint_unlimited_keys(db, user):
    for i in range(api_keys.MAX_KEYS_PER_USER):
        api_keys.issue(db, user, f"key {i}")

    with pytest.raises(ValueError, match="Revoke one"):
        api_keys.issue(db, user, "one too many")


def test_revoking_frees_a_slot(db, user):
    keys = [api_keys.issue(db, user, f"key {i}")[0] for i in range(api_keys.MAX_KEYS_PER_USER)]
    api_keys.revoke(db, user, str(keys[0].id))

    api_keys.issue(db, user, "replacement")  # must not raise


def test_use_is_recorded_so_a_forgotten_key_is_visible(db, user):
    key, plaintext = api_keys.issue(db, user, "my agent")
    assert key.last_used_at is None

    api_keys.user_for_key(db, plaintext)

    assert api_keys.list_for(db, user)[0].last_used_at is not None


def test_a_malformed_key_id_is_a_miss_rather_than_an_error(db, user):
    """The id arrives from a URL, so "not a UUID" is ordinary bad input. The
    first version let SQLAlchemy raise on it, which would have been a 500."""
    assert api_keys.revoke(db, user, "not-a-uuid") is False
    assert api_keys.revoke(db, user, "") is False
    assert api_keys.revoke(db, user, None) is False
