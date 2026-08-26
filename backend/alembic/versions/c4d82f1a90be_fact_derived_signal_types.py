"""fact derived signal types

Adds the Phase 5 signal types produced by arithmetic threshold rules over
structured_facts (see app/engine/fact_rules.py), the first signals in the
project that involve no model call.

Rebuilds the enum rather than using ALTER TYPE ... ADD VALUE, for the same
reason as a7c31e94b02f: ADD VALUE has no honest downgrade, since Postgres
cannot drop a label from an enum.

Revision ID: c4d82f1a90be
Revises: f6189bcdea68
Create Date: 2026-08-25 20:15:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'c4d82f1a90be'
down_revision: Union[str, None] = 'f6189bcdea68'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD = ("SENTIMENT_SHIFT", "NEW_RISK_FACTOR", "NOTABLE_QUOTE", "QOQ_ANOMALY",
        "GUIDANCE_CHANGE", "EMERGING_PATTERN")
_NEW = _OLD + ("INSIDER_ACTIVITY", "SHORT_INTEREST_SPIKE")


def _rebuild(values: tuple[str, ...]) -> None:
    labels = ", ".join(f"'{v}'" for v in values)
    op.execute(f"CREATE TYPE signal_type_new AS ENUM ({labels})")
    op.execute(
        "ALTER TABLE signals ALTER COLUMN signal_type TYPE signal_type_new "
        "USING signal_type::text::signal_type_new"
    )
    op.execute("DROP TYPE signal_type")
    op.execute("ALTER TYPE signal_type_new RENAME TO signal_type")


def upgrade() -> None:
    _rebuild(_NEW)


def downgrade() -> None:
    op.execute(
        "DELETE FROM signals WHERE signal_type IN ('INSIDER_ACTIVITY', 'SHORT_INTEREST_SPIKE')"
    )
    _rebuild(_OLD)
