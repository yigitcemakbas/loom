"""Create the local development account, already verified.

A signed-in account is needed to see most of Loom, and the ordinary route to
one is a six digit code delivered to the bundled mailbox. That is the right
flow for a person and a poor one for a test: it costs three round trips, and
the account it produces disappears the next time the database is rebuilt.

So this creates the same account the sign-up route would, with the email
already marked verified, and it is idempotent. Running it against a database
that already has the account resets the password to the one below and leaves
everything else alone, which is what makes it safe to run from a Makefile
target or after a migration.

**Local only, and deliberately so.** The address ends in .local, which cannot
receive mail from anywhere, and the password is written down here in the
repository. Both facts make this account useless anywhere that matters and
obvious to anyone reading the file. It is not admin: admin comes from
configuration (LOOM_ADMIN_EMAILS) and is reapplied at startup, so an account
cannot grant itself oversight by existing.

Usage: python -m scripts.seed_dev_account
"""

import logging
import sys
from datetime import datetime, timezone

from app.db.session import SessionLocal
from app.models.account import User
from app.services.passwords import hash_password
from app.services.usernames import fold

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("seed_dev_account")

EMAIL = "tester@loom.local"
USERNAME = "loomtester"
# Written down on purpose. See the module docstring: a password in a public
# repository is only safe when the account it opens cannot exist anywhere real,
# and both halves of that are true here.
PASSWORD = "loom-local-testing-2026"


def main() -> int:
    db = SessionLocal()
    try:
        existing = (
            db.query(User).filter(User.username_lower == fold(USERNAME)).one_or_none()
        )
        if existing is not None:
            existing.password_hash = hash_password(PASSWORD)
            if existing.email_verified_at is None:
                existing.email_verified_at = datetime.now(timezone.utc)
            db.commit()
            logger.info("Reset the password for existing account %s.", USERNAME)
            return 0

        user = User(
            email=EMAIL,
            username=USERNAME,
            username_lower=fold(USERNAME),
            password_hash=hash_password(PASSWORD),
            # Verified outright rather than issuing a code nobody will read.
            # The verification flow is exercised by its own tests; making every
            # developer walk through it to see a company page is friction with
            # no coverage attached.
            email_verified_at=datetime.now(timezone.utc),
        )
        db.add(user)
        db.commit()
        logger.info("Created %s (%s). Password: %s", USERNAME, EMAIL, PASSWORD)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
