"""Store quantitative factor scores.

Loom holds tens of thousands of reported figures for the whole universe and,
until this table existed, read none of them: the deterministic rule layer
covered insider trades and short interest only, so the financial statements
themselves were ingested and never used.

A row is one company's reading on one factor as of one date, with the
percentile it earned against every company measurable that day. The universe
size is stored beside it because a decile drawn from twenty-five companies and
one drawn from a hundred and twenty are different claims.

Revision ID: d81c3e05af44
Revises: c4a1f70b28de
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d81c3e05af44"
down_revision = "c4a1f70b28de"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "factor_scores",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("as_of_date", sa.Date(), nullable=False),
        sa.Column("factor_key", sa.String(), nullable=False),
        sa.Column("value", sa.Float(), nullable=False),
        sa.Column("percentile", sa.Float(), nullable=True),
        sa.Column("universe_size", sa.Integer(), nullable=True),
        sa.Column("inputs", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("computed_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "as_of_date", "factor_key", name="uq_factor_scores_point"),
    )
    op.create_index("ix_factor_scores_company_id", "factor_scores", ["company_id"])
    op.create_index("ix_factor_scores_as_of_date", "factor_scores", ["as_of_date"])
    op.create_index("ix_factor_scores_factor_key", "factor_scores", ["factor_key"])


def downgrade() -> None:
    op.drop_index("ix_factor_scores_factor_key", table_name="factor_scores")
    op.drop_index("ix_factor_scores_as_of_date", table_name="factor_scores")
    op.drop_index("ix_factor_scores_company_id", table_name="factor_scores")
    op.drop_table("factor_scores")
