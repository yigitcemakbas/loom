"""Record agent decisions and their reasoning, for the paired trial.

Six agents — a professional, an amateur and a gambler, each run with Loom and
without — can never produce a return result that clears a statistical bar. The
sample is hopeless by construction. What they can produce is reasoning a grader
can assess blind, which is why `rationale` is NOT NULL and the trade itself is
almost incidental.

`loom_points_used` exists to separate "had Loom" from "used Loom". Without it a
favourable result cannot be attributed to the tool rather than to the agent
already knowing the company.

Revision ID: e21f8a47c3b9
Revises: d94c2e60fa17
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e21f8a47c3b9"
down_revision = "d94c2e60fa17"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "agent_decisions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("run_id", sa.String(), nullable=False),
        sa.Column("cohort", sa.String(), nullable=False),
        sa.Column("used_loom", sa.Boolean(), nullable=False),
        sa.Column("agent_label", sa.String(), nullable=True),
        sa.Column(
            "company_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("action", sa.String(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=True),
        sa.Column("conviction", sa.Float(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("would_change_mind", sa.Text(), nullable=True),
        sa.Column("loom_points_used", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("loom_snapshot", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("price_at_decision", sa.Float(), nullable=True),
        sa.Column("forward_return_pct", sa.Float(), nullable=True),
        sa.Column("benchmark_return_pct", sa.Float(), nullable=True),
        sa.Column("abnormal_return_pct", sa.Float(), nullable=True),
        sa.Column("scored_sessions", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
    )
    op.create_index("ix_agent_decisions_run_id", "agent_decisions", ["run_id"])
    op.create_index("ix_agent_decisions_company_id", "agent_decisions", ["company_id"])
    op.create_index("ix_agent_decisions_decided_at", "agent_decisions", ["decided_at"])
    op.create_index("ix_agent_decisions_run_cohort", "agent_decisions", ["run_id", "cohort"])


def downgrade() -> None:
    op.drop_table("agent_decisions")
