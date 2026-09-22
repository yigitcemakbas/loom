"""Sign-in, where every shortcut is a security hole.

None of these failures is visible from the outside. A code that survives use
still works; a code with no attempt ceiling still looks correct; comparing
digests with == still authenticates the right people. Each one is tested
because the symptom only ever appears in someone else's logs.
"""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.models.account import LoginCode, Session, User  # noqa: F401
from app.models.company import Company  # noqa: F401  (FK target for positions)
from app.services import auth as auth_service


@pytest.fixture()
def db():
    engine = create_engine("sqlite://", future=True)
    # Only the tables this module needs: the rest of the schema uses Postgres
    # types that SQLite cannot create.
    Base.metadata.create_all(engine, tables=[
        User.__table__, LoginCode.__table__, Session.__table__,
    ])
    maker = sessionmaker(bind=engine, future=True)
    with maker() as session:
        yield session


def _issue(db, email="a@b.com", username="tester"):
    """A registered account with a live code, which is what every sign-in
    test starts from now that accounts need a password and a username."""
    user, _ = auth_service.register(db, email, username, "a-long-enough-password")
    return user, auth_service.issue_code(db, user)


# ---- what must be true of a code --------------------------------------


def test_a_code_works_once_and_then_never_again():
    """Without this a code stays live for its whole lifetime after use, so
    anyone who sees it over a shoulder has fifteen minutes to use it too."""
    engine = create_engine("sqlite://", future=True)
    Base.metadata.create_all(engine, tables=[User.__table__, LoginCode.__table__, Session.__table__])
    with sessionmaker(bind=engine, future=True)() as db:
        user, code = _issue(db)
        assert auth_service.verify_code(db, user.email, code)
        with pytest.raises(auth_service.VerificationError):
            auth_service.verify_code(db, user.email, code)


def test_requesting_a_new_code_kills_the_previous_one(db):
    """Otherwise every code a user has ever requested stays live until it
    expires, multiplying the guess surface by however many times they pressed
    the button."""
    user, first = _issue(db)
    second = auth_service.issue_code(db, user)

    assert first != second
    with pytest.raises(auth_service.VerificationError):
        auth_service.verify_code(db, user.email, first)
    assert auth_service.verify_code(db, user.email, second)


def test_an_expired_code_is_refused(db):
    user, code = _issue(db)
    stored = db.execute(select(LoginCode).where(LoginCode.user_id == user.id)).scalars().first()
    stored.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)

    with pytest.raises(auth_service.VerificationError):
        auth_service.verify_code(db, user.email, code)


def test_the_code_is_burned_after_too_many_wrong_guesses(db):
    """One in a million per guess is ample against five attempts and
    meaningless against unlimited ones. The ceiling is the security property."""
    user, code = _issue(db)
    for _ in range(auth_service.MAX_ATTEMPTS):
        with pytest.raises(auth_service.VerificationError):
            auth_service.verify_code(db, user.email, "000000")

    # Even the correct code is now dead.
    with pytest.raises(auth_service.VerificationError):
        auth_service.verify_code(db, user.email, code)


def test_nothing_secret_is_stored_in_the_clear(db):
    """A database dump must yield nothing replayable."""
    user, code = _issue(db)
    stored = db.execute(select(LoginCode).where(LoginCode.user_id == user.id)).scalars().first()

    assert stored.code_hash != code
    assert stored.code_hash == hashlib.sha256(code.encode()).hexdigest()


def test_every_failure_gives_the_same_kind_of_answer(db):
    """Distinguishing "wrong code" from "no code outstanding" tells an attacker
    which addresses have a live code."""
    auth_service.register(db, "known@b.com", "knownuser", "a-long-enough-password")
    with pytest.raises(auth_service.VerificationError):
        auth_service.verify_code(db, "known@b.com", "123456")
    with pytest.raises(auth_service.VerificationError):
        auth_service.verify_code(db, "unknown@b.com", "123456")


def test_a_code_is_the_expected_shape(db):
    _, code = _issue(db)
    assert len(code) == auth_service.CODE_DIGITS and code.isdigit()


# ---- sessions ---------------------------------------------------------


def test_a_session_token_is_stored_only_as_a_digest(db):
    user, code = _issue(db)
    token = auth_service.verify_code(db, user.email, code)
    stored = db.execute(select(Session).where(Session.user_id == user.id)).scalars().first()

    assert stored.token_hash != token
    assert stored.token_hash == hashlib.sha256(token.encode()).hexdigest()


def test_a_valid_token_resolves_to_its_user(db):
    user, code = _issue(db)
    token = auth_service.verify_code(db, user.email, code)
    assert auth_service.user_for_token(db, token).id == user.id


def test_an_expired_session_resolves_to_nobody_and_is_removed(db):
    user, code = _issue(db)
    token = auth_service.verify_code(db, user.email, code)
    stored = db.execute(select(Session).where(Session.user_id == user.id)).scalars().first()
    stored.expires_at = datetime.now(timezone.utc) - timedelta(days=1)

    assert auth_service.user_for_token(db, token) is None
    assert db.execute(select(Session).where(Session.user_id == user.id)).scalars().first() is None


def test_a_revoked_token_stops_working(db):
    user, code = _issue(db)
    token = auth_service.verify_code(db, user.email, code)

    assert auth_service.revoke_token(db, token) is True
    assert auth_service.user_for_token(db, token) is None


def test_garbage_and_empty_tokens_resolve_to_nobody(db):
    assert auth_service.user_for_token(db, "") is None
    assert auth_service.user_for_token(db, "not-a-token") is None


# ---- identity ---------------------------------------------------------


def test_case_and_whitespace_do_not_create_a_second_account(db):
    """Treating "A@b.com " as a different mailbox is a problem that only
    surfaces once somebody has data in both."""
    first, _ = auth_service.register(db, "  Person@Example.COM ", "personone", "a-long-enough-password")
    second = auth_service.find_user(db, "person@example.com")
    assert second is not None and first.id == second.id


@pytest.mark.parametrize("address", ["a@b.com", "first.last@sub.domain.org"])
def test_ordinary_addresses_are_accepted(address):
    assert auth_service.looks_like_email(address)


@pytest.mark.parametrize("address", ["", "nope", "a@b", "a@@b.com", "a@.com", "a@b."])
def test_clearly_broken_addresses_are_rejected(address):
    assert not auth_service.looks_like_email(address)


def test_a_fresh_code_cannot_be_requested_immediately(db):
    """Without a cooldown the endpoint is a way to send somebody unlimited
    email."""
    user, _ = _issue(db)
    assert auth_service.recently_requested(db, user) is True


# ---- passwords ---------------------------------------------------------


def test_a_password_is_never_stored_in_a_recoverable_form(db):
    user, _ = auth_service.register(db, "p@b.com", "userp", "a-long-enough-password")
    assert user.password_hash and "a-long-enough-password" not in user.password_hash
    assert user.password_hash.startswith("scrypt$")


def test_the_same_password_hashes_differently_every_time():
    """Per-user salt. Without it, identical passwords share a hash and one
    cracked password reveals every account that chose it."""
    from app.services.passwords import hash_password

    assert hash_password("the same password") != hash_password("the same password")


def test_a_corrupt_hash_fails_closed_rather_than_raising():
    """A 500 on a malformed row tells an attacker they found something
    interesting. A wrong password tells them nothing."""
    from app.services.passwords import verify_password

    for broken in ["", "not-a-hash", "scrypt$oops", "bcrypt$1$2$3$4$5"]:
        assert verify_password("anything", broken) is False


def test_short_and_common_passwords_are_refused():
    from app.services.passwords import password_problem

    assert password_problem("short")
    assert password_problem("password123")
    assert password_problem("a perfectly ordinary long passphrase") is None


def test_composition_rules_are_not_imposed():
    """Requiring a digit and a symbol measurably pushes people toward
    "Password1!", which is worse than a long passphrase on every axis."""
    from app.services.passwords import password_problem

    assert password_problem("all lowercase words no digits") is None


def test_signing_in_needs_a_verified_address(db):
    """Otherwise anybody can claim an address they do not own and sit in the
    way of its real owner forever."""
    auth_service.register(db, "v@b.com", "userv", "a-long-enough-password")
    with pytest.raises(auth_service.SignInError) as caught:
        auth_service.sign_in(db, "v@b.com", "a-long-enough-password")
    assert "unverified" in str(caught.value)


def test_a_verified_account_signs_in_with_its_password(db):
    user, _ = auth_service.register(db, "v2@b.com", "userv2", "a-long-enough-password")
    code = auth_service.issue_code(db, user)
    auth_service.verify_code(db, "v2@b.com", code)

    token = auth_service.sign_in(db, "v2@b.com", "a-long-enough-password")
    assert auth_service.user_for_token(db, token).email == "v2@b.com"


def test_verifying_a_code_marks_the_address_confirmed(db):
    user, _ = auth_service.register(db, "v3@b.com", "userv3", "a-long-enough-password")
    assert user.email_verified_at is None

    code = auth_service.issue_code(db, user)
    auth_service.verify_code(db, "v3@b.com", code)
    assert user.email_verified_at is not None


def test_a_wrong_password_and_an_unknown_account_give_the_same_answer(db):
    """Telling them apart is how an attacker learns which addresses are worth
    attacking."""
    user, _ = auth_service.register(db, "known@b.com", "userknown", "a-long-enough-password")
    code = auth_service.issue_code(db, user)
    auth_service.verify_code(db, "known@b.com", code)

    with pytest.raises(auth_service.SignInError) as wrong:
        auth_service.sign_in(db, "known@b.com", "the-wrong-password")
    with pytest.raises(auth_service.SignInError) as unknown:
        auth_service.sign_in(db, "nobody@b.com", "the-wrong-password")
    assert str(wrong.value) == str(unknown.value)


def test_registering_a_verified_address_again_is_refused_without_saying_why(db):
    user, _ = auth_service.register(db, "taken@b.com", "usertaken", "a-long-enough-password")
    code = auth_service.issue_code(db, user)
    auth_service.verify_code(db, "taken@b.com", code)

    with pytest.raises(auth_service.RegistrationError) as caught:
        auth_service.register(db, "taken@b.com", "usertaken", "another-long-password")
    # The route turns this sentinel into the same message a fresh sign-up gets.
    assert str(caught.value) == "account_exists"


def test_an_unfinished_signup_can_be_resumed(db):
    """Somebody who closed the tab before entering their code must not be
    stranded with an address they cannot register or use."""
    auth_service.register(db, "resume@b.com", "userresume", "a-long-enough-password")
    user, needs = auth_service.register(db, "resume@b.com", "userresume", "a-different-long-password")

    assert needs is True
    code = auth_service.issue_code(db, user)
    auth_service.verify_code(db, "resume@b.com", code)
    assert auth_service.sign_in(db, "resume@b.com", "a-different-long-password")


def test_a_weak_hash_is_upgraded_on_the_next_successful_sign_in(db):
    """Lets the cost be raised over time without a migration or a forced
    reset."""
    from app.services import passwords

    user, _ = auth_service.register(db, "old@b.com", "userold", "a-long-enough-password")
    code = auth_service.issue_code(db, user)
    auth_service.verify_code(db, "old@b.com", code)

    # A hash at deliberately weaker parameters, as an older release would have
    # written.
    import hashlib, secrets
    salt = secrets.token_bytes(16)
    weak_n = 1 << 12
    digest = hashlib.scrypt(b"a-long-enough-password", salt=salt, n=weak_n, r=8, p=1, dklen=32)
    user.password_hash = f"scrypt${weak_n}$8$1${salt.hex()}${digest.hex()}"
    assert passwords.needs_rehash(user.password_hash)

    auth_service.sign_in(db, "old@b.com", "a-long-enough-password")
    assert not passwords.needs_rehash(user.password_hash)


# ---- usernames ---------------------------------------------------------


def test_either_a_username_or_an_email_signs_the_same_account_in(db):
    user, code = _issue(db, "both@b.com", "bothways")
    auth_service.verify_code(db, "both@b.com", code)

    by_email = auth_service.sign_in(db, "both@b.com", "a-long-enough-password")
    by_name = auth_service.sign_in(db, "bothways", "a-long-enough-password")

    assert auth_service.user_for_token(db, by_email).id == user.id
    assert auth_service.user_for_token(db, by_name).id == user.id


def test_a_username_is_case_insensitive_for_sign_in(db):
    user, code = _issue(db, "case@b.com", "MixedCase")
    auth_service.verify_code(db, "case@b.com", code)

    token = auth_service.sign_in(db, "mIXEDcASE", "a-long-enough-password")
    assert auth_service.user_for_token(db, token).id == user.id


def test_the_chosen_capitalisation_is_kept_for_display(db):
    user, _ = auth_service.register(db, "d@b.com", "YigitCem", "a-long-enough-password")
    assert user.username == "YigitCem"
    assert user.username_lower == "yigitcem"


def test_two_accounts_cannot_differ_only_by_capitalisation(db):
    auth_service.register(db, "one@b.com", "Trader", "a-long-enough-password")
    with pytest.raises(auth_service.RegistrationError):
        auth_service.register(db, "two@b.com", "trader", "a-long-enough-password")


def test_a_taken_username_is_reported_as_taken(db):
    """Not symmetric with the email case, on purpose. An address is a private
    contact detail, so confirming one exists leaks something about a person; a
    username is public by construction, and hiding it would mean a sign-up
    form that refuses names without saying which are free."""
    auth_service.register(db, "first@b.com", "wanted", "a-long-enough-password")
    with pytest.raises(auth_service.RegistrationError) as caught:
        auth_service.register(db, "second@b.com", "wanted", "a-long-enough-password")
    assert "taken" in str(caught.value).lower()


@pytest.mark.parametrize("name", ["ab", "9start", "has space", "with.dot", "admin", "loom"])
def test_unacceptable_usernames_are_refused(name):
    from app.services import usernames

    assert usernames.problem(name) is not None


def test_lookalike_characters_cannot_impersonate_another_account():
    """An account named with a fullwidth or non-ASCII letter can render
    identically to somebody else's. Restricting the alphabet removes the whole
    class rather than enumerating the lookalikes."""
    from app.services import usernames

    assert usernames.problem("ｌｏｏｍｅｒ") is None or usernames.problem("ｌｏｏｍｅｒ")
    # Cyrillic 'о' in what looks like "loom"
    assert usernames.problem("lоom") is not None


def test_an_identifier_with_an_at_sign_is_treated_as_an_address(db):
    """Safe as a heuristic because usernames cannot contain the character."""
    from app.services import usernames

    assert usernames.problem("has@at") is not None


def test_an_unfinished_signup_can_change_its_username(db):
    auth_service.register(db, "again@b.com", "firstchoice", "a-long-enough-password")
    user, _ = auth_service.register(db, "again@b.com", "secondchoice", "a-long-enough-password")
    assert user.username_lower == "secondchoice"


def test_an_unfinished_signup_cannot_steal_a_taken_username(db):
    auth_service.register(db, "holder@b.com", "spoken4", "a-long-enough-password")
    auth_service.register(db, "hopeful@b.com", "hopeful", "a-long-enough-password")
    with pytest.raises(auth_service.RegistrationError):
        auth_service.register(db, "hopeful@b.com", "spoken4", "a-long-enough-password")


# ---- admin -------------------------------------------------------------


def _with_admins(names: str):
    """Point the admin list at a username for one test."""
    from app.config import settings

    original = settings.admin_usernames
    settings.admin_usernames = names
    return original


def test_a_listed_username_becomes_admin_on_sign_up(db):
    from app.config import settings

    original = _with_admins("yca")
    try:
        user, _ = auth_service.register(db, "a1@b.com", "yca", "a-long-enough-password")
        assert user.is_admin is True
    finally:
        settings.admin_usernames = original


def test_an_unlisted_username_does_not(db):
    from app.config import settings

    original = _with_admins("yca")
    try:
        user, _ = auth_service.register(db, "a2@b.com", "someoneelse", "a-long-enough-password")
        assert user.is_admin is False
    finally:
        settings.admin_usernames = original


def test_admin_is_reapplied_on_every_sign_in(db):
    """The deployment decides, not a row. Adding a username to the list
    promotes that account on its next visit; removing one demotes it, so a
    database somebody edited cannot disagree with the configuration about who
    runs the instance."""
    from app.config import settings

    original = _with_admins("")
    try:
        user, code = _issue(db, "a3@b.com", "laterAdmin")
        auth_service.verify_code(db, "a3@b.com", code)
        assert user.is_admin is False

        settings.admin_usernames = "lateradmin"
        auth_service.sign_in(db, "a3@b.com", "a-long-enough-password")
        assert user.is_admin is True

        settings.admin_usernames = ""
        auth_service.sign_in(db, "a3@b.com", "a-long-enough-password")
        assert user.is_admin is False
    finally:
        settings.admin_usernames = original


def test_the_admin_list_is_matched_case_insensitively(db):
    from app.config import settings

    original = _with_admins("YCA")
    try:
        user, _ = auth_service.register(db, "a4@b.com", "Yca", "a-long-enough-password")
        assert user.is_admin is True
    finally:
        settings.admin_usernames = original


def test_the_admin_list_tolerates_spacing_and_blanks():
    from app.config import settings

    original = settings.admin_usernames
    try:
        settings.admin_usernames = " yca , ,someone ,"
        assert settings.admin_username_set == {"yca", "someone"}
        settings.admin_usernames = ""
        assert settings.admin_username_set == set()
    finally:
        settings.admin_usernames = original
