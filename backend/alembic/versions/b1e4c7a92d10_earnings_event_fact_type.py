"""earnings event fact type

Adds `earnings_event` so scheduled earnings dates, consensus estimates, and
delivered results can land in structured_facts alongside insider transactions.

Rebuilds the enum rather than ALTER TYPE ... ADD VALUE, which has no honest
downgrade: Postgres cannot drop a label from an enum.

Revision ID: b1e4c7a92d10
Revises: 8a6c5fd75e9c
Create Date: 2026-08-26 15:10:00.000000

"""
from typing import Sequence, Union

from alembic import op

revision: str = 'b1e4c7a92d10'
down_revision: Union[str, None] = '8a6c5fd75e9c'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = ("INSIDER_TRANSACTION", "INSTITUTIONAL_HOLDING", "SHORT_INTEREST",
        "PATENT_FILING", "SEARCH_TREND_INDEX", "JOB_POSTING_COUNT", "APP_STORE_RANKING")
_NEW = ("INSIDER_TRANSACTION", "INSTITUTIONAL_HOLDING", "SHORT_INTEREST", "EARNINGS_EVENT",
        "PATENT_FILING", "SEARCH_TREND_INDEX", "JOB_POSTING_COUNT", "APP_STORE_RANKING")


def _rebuild(values: tuple[str, ...]) -> None:
    labels = ", ".join(f"'{v}'" for v in values)
    op.execute(f"CREATE TYPE fact_type_new AS ENUM ({labels})")
    op.execute(
        "ALTER TABLE structured_facts ALTER COLUMN fact_type TYPE fact_type_new "
        "USING fact_type::text::fact_type_new"
    )
    op.execute("DROP TYPE fact_type")
    op.execute("ALTER TYPE fact_type_new RENAME TO fact_type")


def upgrade() -> None:
    _rebuild(_NEW)


def downgrade() -> None:
    op.execute("DELETE FROM structured_facts WHERE fact_type = 'EARNINGS_EVENT'")
    _rebuild(_OLD)
