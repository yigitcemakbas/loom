"""Store daily closes.

Prices were fetched on demand for a chart and never written down, which blocked
valuation (a ratio between figures and price), momentum (a function of past
prices), and any reproducible evaluation (the harness could only measure
findings whose prices the provider still happened to serve).

Two closes because they answer different questions: `close` is what the share
traded at and pairs with a share count to give a market capitalisation;
`adjusted_close` is restated for splits and dividends and is the only honest
basis for a return across time.

Revision ID: e93b7d21ca60
Revises: d81c3e05af44
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e93b7d21ca60"
down_revision = "d81c3e05af44"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "price_bars",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("companies.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("session_date", sa.Date(), nullable=False),
        sa.Column("close", sa.Float(), nullable=False),
        sa.Column("adjusted_close", sa.Float(), nullable=False),
        sa.Column("volume", sa.Float(), nullable=True),
        sa.Column("source", sa.String(), nullable=False, server_default="yahoo"),
        sa.Column("fetched_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("company_id", "session_date", name="uq_price_bars_session"),
    )
    op.create_index("ix_price_bars_company_id", "price_bars", ["company_id"])
    op.create_index("ix_price_bars_session_date", "price_bars", ["session_date"])


def downgrade() -> None:
    op.drop_index("ix_price_bars_session_date", table_name="price_bars")
    op.drop_index("ix_price_bars_company_id", table_name="price_bars")
    op.drop_table("price_bars")
