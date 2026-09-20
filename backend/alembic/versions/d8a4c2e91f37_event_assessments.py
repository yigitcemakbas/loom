"""Durable record of live event assessments.

Revision ID: d8a4c2e91f37
Revises: c7f3a1b20e45
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d8a4c2e91f37"
down_revision = "c7f3a1b20e45"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "event_assessments",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "prior_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("company_priors.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("external_id", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("form", sa.String(), nullable=True),
        sa.Column("source_url", sa.String(), nullable=True),
        sa.Column("score", sa.Float(), nullable=False),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("headline", sa.Text(), nullable=False),
        sa.Column("matches", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("surprises", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("amplifiers", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "assessed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("latency_seconds", sa.Float(), nullable=True),
        sa.Column("scoring_ms", sa.Float(), nullable=True),
        sa.UniqueConstraint("company_id", "external_id", name="uq_event_assessment_external"),
    )
    op.create_index("ix_event_assessments_company_id", "event_assessments", ["company_id"])
    op.create_index("ix_event_assessments_occurred_at", "event_assessments", ["occurred_at"])
    op.create_index("ix_event_assessments_assessed_at", "event_assessments", ["assessed_at"])


def downgrade() -> None:
    op.drop_index("ix_event_assessments_assessed_at", table_name="event_assessments")
    op.drop_index("ix_event_assessments_occurred_at", table_name="event_assessments")
    op.drop_index("ix_event_assessments_company_id", table_name="event_assessments")
    op.drop_table("event_assessments")
