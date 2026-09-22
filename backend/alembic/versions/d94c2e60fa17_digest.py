"""Let Loom reach somebody who is not looking at it.

The change feed answers "what moved since I last looked" and still requires
remembering to look, which is the failure it was built to fix moved one level
up. A digest closes it.

`digest_sent_at` records the moment covered up to rather than the time of the
last send. The next digest reports changes since that point, so a day the
machine was asleep is caught up on the next run instead of silently skipped,
and nothing is ever reported twice.

Revision ID: d94c2e60fa17
Revises: c5e1b7f2a380
"""

import sqlalchemy as sa
from alembic import op

revision = "d94c2e60fa17"
down_revision = "c5e1b7f2a380"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("digest_frequency", sa.String(), nullable=False, server_default="daily"),
    )
    op.add_column(
        "users",
        sa.Column("digest_sent_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("users", "digest_sent_at")
    op.drop_column("users", "digest_frequency")
