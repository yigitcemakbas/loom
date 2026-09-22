"""Add passwords and email verification to accounts.

The first cut of accounts was passwordless: an emailed code was both the proof
of identity and the whole sign-in. That is a defensible design and it is not
what this product needs. A code-only sign-in means every single visit requires
a round trip through a mailbox, which on an install with no SMTP means reading
the server log to open your own portfolio.

So the two jobs are separated. A password is what you sign in with. The code
proves, once, that the address is really yours, which is what stops somebody
claiming an address they do not own and blocking its real owner from ever
registering it.

Revision ID: a7d4e91b3c05
Revises: f1a2c8e04b77
"""

import sqlalchemy as sa
from alembic import op

revision = "a7d4e91b3c05"
down_revision = "f1a2c8e04b77"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("users", sa.Column("password_hash", sa.String(), nullable=True))
    op.add_column(
        "users", sa.Column("email_verified_at", sa.DateTime(timezone=True), nullable=True)
    )
    # Accounts created before passwords existed have no way to sign in and no
    # owner who expects otherwise, so they are removed rather than left as rows
    # that can never be used and can never be re-registered.
    op.execute("DELETE FROM users WHERE password_hash IS NULL")


def downgrade() -> None:
    op.drop_column("users", "email_verified_at")
    op.drop_column("users", "password_hash")
