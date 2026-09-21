"""Add the FUNDAMENTAL fact type for SEC XBRL financials.

Revision ID: f2c9d6a41b83
Revises: e5b81c4a7d92
"""

from alembic import op

revision = "f2c9d6a41b83"
down_revision = "e5b81c4a7d92"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE fact_type ADD VALUE IF NOT EXISTS 'FUNDAMENTAL'")


def downgrade() -> None:
    # PostgreSQL cannot remove an enum value without rewriting the type and
    # every dependent column, so the rows are deleted rather than the value.
    op.execute("DELETE FROM structured_facts WHERE fact_type = 'FUNDAMENTAL'")
