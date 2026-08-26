"""emerging pattern signal type

Adds the Phase 3 short-window synthesis signal type (see
app/engine/clustering.py) to the signal_type enum.

The enum is rebuilt rather than extended with ALTER TYPE ... ADD VALUE. Two
reasons: a rebuild is reversible (Postgres cannot drop a value from an enum,
so ADD VALUE has no honest downgrade), and it lets this migration also clear a
stray lowercase 'emerging_pattern' label left behind by an earlier attempt.

Note the casing. SQLAlchemy's Enum type persists the Python member *name*, not
its value, so the stored labels are 'EMERGING_PATTERN', not 'emerging_pattern'.

Revision ID: a7c31e94b02f
Revises: dd2ea0f05388
Create Date: 2026-08-25 03:10:00.000000

"""
from typing import Sequence, Union

from alembic import op


# revision identifiers, used by Alembic.
revision: str = 'a7c31e94b02f'
down_revision: Union[str, None] = 'dd2ea0f05388'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_OLD_VALUES = ("SENTIMENT_SHIFT", "NEW_RISK_FACTOR", "NOTABLE_QUOTE", "QOQ_ANOMALY", "GUIDANCE_CHANGE")
_NEW_VALUES = _OLD_VALUES + ("EMERGING_PATTERN",)


def _rebuild(values: tuple[str, ...]) -> None:
    labels = ", ".join(f"'{value}'" for value in values)
    op.execute(f"CREATE TYPE signal_type_new AS ENUM ({labels})")
    op.execute(
        "ALTER TABLE signals ALTER COLUMN signal_type TYPE signal_type_new "
        "USING signal_type::text::signal_type_new"
    )
    op.execute("DROP TYPE signal_type")
    op.execute("ALTER TYPE signal_type_new RENAME TO signal_type")


def upgrade() -> None:
    _rebuild(_NEW_VALUES)


def downgrade() -> None:
    # Rows of the dropped type must go before the type can lose the label.
    op.execute("DELETE FROM signals WHERE signal_type = 'EMERGING_PATTERN'")
    _rebuild(_OLD_VALUES)
