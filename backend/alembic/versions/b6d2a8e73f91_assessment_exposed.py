"""Record which tracked companies an event propagates to.

Revision ID: b6d2a8e73f91
Revises: a3e7f1c95d24
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b6d2a8e73f91"
down_revision = "a3e7f1c95d24"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "event_assessments",
        sa.Column("exposed", postgresql.JSONB(), nullable=False, server_default="[]"),
    )


def downgrade() -> None:
    op.drop_column("event_assessments", "exposed")
