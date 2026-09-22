"""Password hashing.

`hashlib.scrypt`, from the standard library. The choice is deliberate on two
counts.

**Memory-hard, not just slow.** A plain SHA or a single-round PBKDF2 is cheap
to parallelise on a GPU, where an attacker gets tens of thousands of guesses
for the price of one. scrypt's cost parameter forces each guess to allocate
real memory, which is the resource GPUs have least of, so the gap between the
defender's cost and the attacker's stays narrow.

**No dependency.** bcrypt and argon2 are both fine and both mean a native
wheel, a version to track and a supply-chain surface for something that has to
keep working for years. scrypt ships with Python and does the job.

Each hash stores the parameters it was made with, so raising the cost later
does not invalidate existing passwords: an old hash still verifies against its
own parameters, and `needs_rehash` says when to quietly upgrade one on the next
successful sign-in.
"""

import hashlib
import hmac
import secrets

# Cost parameters. n is the work factor and doubling it doubles both time and
# memory; 2^15 with r=8 costs roughly 32MB and a few tens of milliseconds per
# hash, which is unnoticeable to a person signing in and expensive to an
# attacker doing it a billion times.
SCRYPT_N = 1 << 15
SCRYPT_R = 8
SCRYPT_P = 1
SALT_BYTES = 16
KEY_BYTES = 32

# scrypt needs a memory ceiling at least n * r * 128 bytes; the default is too
# low for these parameters and raises a ValueError rather than degrading.
MAX_MEMORY = SCRYPT_N * SCRYPT_R * 256

MIN_PASSWORD_LENGTH = 10


def hash_password(password: str) -> str:
    """A self-describing hash: algorithm, parameters, salt, digest."""
    salt = secrets.token_bytes(SALT_BYTES)
    digest = hashlib.scrypt(
        password.encode("utf-8"), salt=salt,
        n=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P, dklen=KEY_BYTES, maxmem=MAX_MEMORY,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Check a password against a stored hash.

    Returns False for anything malformed rather than raising: a corrupted row
    must fail closed as a wrong password, not as a 500 that tells an attacker
    they found something interesting.
    """
    if not stored:
        return False
    try:
        algorithm, n, r, p, salt_hex, digest_hex = stored.split("$")
        if algorithm != "scrypt":
            return False
        expected = bytes.fromhex(digest_hex)
        actual = hashlib.scrypt(
            password.encode("utf-8"), salt=bytes.fromhex(salt_hex),
            n=int(n), r=int(r), p=int(p), dklen=len(expected),
            maxmem=int(n) * int(r) * 256,
        )
    except (ValueError, TypeError):
        return False
    # Constant time: a plain == leaks how much of the digest matched.
    return hmac.compare_digest(expected, actual)


def needs_rehash(stored: str) -> bool:
    """Whether a hash was made with weaker parameters than we now use.

    Lets the cost be raised over time without a migration or a forced reset:
    the next successful sign-in silently re-hashes at current strength.
    """
    try:
        algorithm, n, r, p, _, _ = stored.split("$")
    except (ValueError, AttributeError):
        return True
    return algorithm != "scrypt" or (int(n), int(r), int(p)) != (SCRYPT_N, SCRYPT_R, SCRYPT_P)


def password_problem(password: str) -> str | None:
    """Why this password is unacceptable, or None.

    Length is the only hard rule. Composition requirements (a digit, a symbol,
    a capital) measurably push people toward "Password1!" and its cousins,
    which is worse than a long passphrase on every axis that matters. The
    common-password check catches the failures that composition rules are
    actually aiming at.
    """
    if len(password) < MIN_PASSWORD_LENGTH:
        return f"Use at least {MIN_PASSWORD_LENGTH} characters."
    if len(password) > 256:
        # Not a security limit, a denial-of-service one: scrypt on a megabyte
        # of input is a free way to occupy the server.
        return "That password is too long."
    if password.lower() in _COMMON:
        return "That password is too common. Pick something else."
    return None


# The handful that appear at the top of every breach corpus. Not a substitute
# for a real list, and enough to stop the worst case on a tool like this.
_COMMON = frozenset({
    "password", "password1", "password123", "123456789", "1234567890",
    "qwertyuiop", "letmein123", "iloveyou1", "administrator", "welcome123",
    "abc123456", "passw0rd1", "trustno1234", "monkey12345", "qwerty12345",
})


__all__ = [
    "MIN_PASSWORD_LENGTH",
    "hash_password",
    "needs_rehash",
    "password_problem",
    "verify_password",
]
