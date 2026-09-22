"""Username rules.

A username is two things at once and they pull in different directions. It is
how a person is addressed, which wants the capitalisation they chose; and it is
a lookup key, which wants exactly one spelling. So the display form is kept as
typed and uniqueness is enforced on a folded copy, the same split GitHub and
most others settled on.

The folding is not `lower()` alone. Unicode has several ways to write
characters that render identically, and a username that looks like somebody
else's is the whole attack: an account named `Ioom` with a capital i is
indistinguishable from `loom` in most typefaces. Restricting the alphabet to
ASCII letters, digits, underscore and hyphen removes the entire class rather
than trying to enumerate the lookalikes.
"""

import re
import unicodedata

MIN_LENGTH = 3
MAX_LENGTH = 24

# Must start with a letter, so a username can never be confused for an id, a
# number, or a flag in a URL.
_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")

# Names that would let an account impersonate the product or sit at a path the
# app might want later. Cheap to reserve now, impossible to reclaim once
# somebody holds one.
RESERVED = frozenset({
    "admin", "administrator", "root", "system", "support", "help", "security",
    "loom", "loomapp", "official", "staff", "team", "moderator", "mod",
    "api", "www", "mail", "ftp", "auth", "login", "signin", "signup", "logout",
    "settings", "account", "accounts", "billing", "me", "user", "users",
    "null", "undefined", "none", "anonymous", "guest", "test",
})


def fold(username: str) -> str:
    """The comparison form. Two usernames collide when their folds match."""
    # NFKC first, so visually identical compatibility characters (fullwidth
    # letters, for instance) collapse before the alphabet check sees them.
    return unicodedata.normalize("NFKC", username or "").strip().lower()


def problem(username: str) -> str | None:
    """Why this username is unacceptable, or None.

    Phrased for a person choosing one, not for a log.
    """
    folded = fold(username)
    if not folded:
        return "Pick a username."
    if len(folded) < MIN_LENGTH:
        return f"Usernames are at least {MIN_LENGTH} characters."
    if len(folded) > MAX_LENGTH:
        return f"Usernames are at most {MAX_LENGTH} characters."
    if not _PATTERN.match(folded):
        return (
            "Use letters, numbers, underscores and hyphens, starting with a letter."
        )
    if folded in RESERVED:
        return "That username is reserved. Pick another."
    return None


def suggest_from_email(email: str, taken: set[str]) -> str:
    """A usable username derived from an address, for backfilling old rows.

    Only used where an account predates usernames. A person choosing their own
    should always be asked rather than assigned one.
    """
    local = fold(email.split("@", 1)[0])
    cleaned = re.sub(r"[^a-z0-9_-]", "", local).lstrip("0123456789_-")
    base = (cleaned or "user")[:MAX_LENGTH - 3]
    if len(base) < MIN_LENGTH:
        base = (base + "user")[:MAX_LENGTH - 3]

    candidate = base
    suffix = 1
    while candidate in taken or candidate in RESERVED:
        suffix += 1
        candidate = f"{base}{suffix}"
    return candidate


__all__ = ["MAX_LENGTH", "MIN_LENGTH", "RESERVED", "fold", "problem", "suggest_from_email"]
