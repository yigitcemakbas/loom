"""Two-tier universe and standing company priors.

Two changes that serve the same goal from opposite ends. `companies.tier`
decides how much a company costs to cover, which is what makes a universe of
hundreds affordable alongside deep coverage of a few. `company_priors` stores
what Loom worked out about a focus company in advance, so that reacting to an
event is arithmetic rather than a model call.

Revision ID: c7f3a1b20e45
Revises: 34782d5e0ea5
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c7f3a1b20e45"
down_revision = "34782d5e0ea5"
branch_labels = None
depends_on = None


def upgrade() -> None:
    company_tier = postgresql.ENUM("FOCUS", "WIDE", name="company_tier")
    company_tier.create(op.get_bind(), checkfirst=True)

    op.add_column(
        "companies",
        sa.Column(
            "tier",
            company_tier,
            nullable=False,
            server_default="WIDE",
        ),
    )
    op.create_index("ix_companies_tier", "companies", ["tier"])

    # Everything that existed before the tiers did was being covered in full,
    # so preserve that rather than silently downgrading live coverage.
    op.execute("UPDATE companies SET tier = 'FOCUS'")

    op.create_table(
        "company_priors",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("watch_items", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("expectations", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("positioning", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("already_priced", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("source_signal_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("prompt_version", sa.String(), nullable=False),
        sa.Column("engine_version", sa.String(), nullable=False),
        sa.Column(
            "generated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index("ix_company_priors_company_id", "company_priors", ["company_id"])
    op.create_index("ix_company_priors_generated_at", "company_priors", ["generated_at"])


def downgrade() -> None:
    op.drop_index("ix_company_priors_generated_at", table_name="company_priors")
    op.drop_index("ix_company_priors_company_id", table_name="company_priors")
    op.drop_table("company_priors")
    op.drop_index("ix_companies_tier", table_name="companies")
    op.drop_column("companies", "tier")
    postgresql.ENUM(name="company_tier").drop(op.get_bind(), checkfirst=True)
