"""Give accounts usernames.

An email address is an identifier and a poor name: it is somebody's private
contact detail, it is long, and showing it in a sidebar on a shared screen
discloses more than the person meant to. A username is what an account is
called, and what it can also sign in with.

Two columns for one name. The display form keeps the capitalisation that was
chosen; the folded form carries the unique index, so "Yigit" and "yigit" cannot
both exist and either spelling reaches the same account. Folding on lookup
alone would leave the database able to hold both.

Existing rows are backfilled from the local part of their address rather than
dropped, since by this point an account may already own positions.

Revision ID: b3f8a21d9e64
Revises: a7d4e91b3c05
"""

import re

import sqlalchemy as sa
from alembic import op

revision = "b3f8a21d9e64"
down_revision = "a7d4e91b3c05"
branch_labels = None
depends_on = None


def _derive(email: str, taken: set[str]) -> str:
    local = (email or "").split("@", 1)[0].lower()
    cleaned = re.sub(r"[^a-z0-9_-]", "", local).lstrip("0123456789_-")
    base = (cleaned or "user")[:21] or "user"
    if len(base) < 3:
        base = (base + "user")[:21]
    candidate, suffix = base, 1
    while candidate in taken:
        suffix += 1
        candidate = f"{base}{suffix}"
    taken.add(candidate)
    return candidate


def upgrade() -> None:
    # Added nullable, backfilled, then constrained: adding a NOT NULL unique
    # column to a table with rows in it fails outright.
    op.add_column("users", sa.Column("username", sa.String(), nullable=True))
    op.add_column("users", sa.Column("username_lower", sa.String(), nullable=True))

    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, email FROM users")).fetchall()
    taken: set[str] = set()
    for row in rows:
        username = _derive(row.email, taken)
        connection.execute(
            sa.text("UPDATE users SET username = :u, username_lower = :u WHERE id = :i"),
            {"u": username, "i": row.id},
        )

    op.alter_column("users", "username", nullable=False)
    op.alter_column("users", "username_lower", nullable=False)
    op.create_index("ix_users_username_lower", "users", ["username_lower"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_users_username_lower", table_name="users")
    op.drop_column("users", "username_lower")
    op.drop_column("users", "username")
