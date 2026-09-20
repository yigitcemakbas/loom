"""Add the WATCH coverage tier.

Revision ID: e5b81c4a7d92
Revises: d8a4c2e91f37
"""

from alembic import op

revision = "e5b81c4a7d92"
down_revision = "d8a4c2e91f37"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Outside the migration's transaction: PostgreSQL will not allow a newly
    # added enum value to be used in the same transaction that added it, and
    # an autocommit block keeps this safe to run before any backfill that
    # wants to use it.
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE company_tier ADD VALUE IF NOT EXISTS 'WATCH'")


def downgrade() -> None:
    # PostgreSQL cannot drop a value from an enum type. Removing it would mean
    # recreating the type and rewriting every dependent column, which is a
    # destructive operation to perform automatically on the way down, so any
    # company on the removed tier is moved to the cheaper neighbour instead.
    op.execute("UPDATE companies SET tier = 'WIDE' WHERE tier = 'WATCH'")
