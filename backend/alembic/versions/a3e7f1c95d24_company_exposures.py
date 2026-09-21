"""Dependency edges between companies, for propagating an event downstream.

Revision ID: a3e7f1c95d24
Revises: f2c9d6a41b83
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a3e7f1c95d24"
down_revision = "f2c9d6a41b83"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "company_exposures",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "dependent_company_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "hub_company_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("mention_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("source", sa.String(), nullable=False, server_default="sec-edgar-fts"),
        sa.Column(
            "computed_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.UniqueConstraint("dependent_company_id", "hub_company_id", name="uq_exposure_pair"),
    )
    op.create_index("ix_exposure_dependent", "company_exposures", ["dependent_company_id"])
    op.create_index("ix_exposure_hub", "company_exposures", ["hub_company_id"])
    op.create_index("ix_exposure_computed_at", "company_exposures", ["computed_at"])


def downgrade() -> None:
    op.drop_index("ix_exposure_computed_at", table_name="company_exposures")
    op.drop_index("ix_exposure_hub", table_name="company_exposures")
    op.drop_index("ix_exposure_dependent", table_name="company_exposures")
    op.drop_table("company_exposures")
