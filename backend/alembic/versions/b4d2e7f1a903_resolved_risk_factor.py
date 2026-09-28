"""Add RESOLVED_RISK_FACTOR to the signal type enum.

The year-over-year risk comparison only ever ran one way. It extracted Item 1A
from both filings and iterated the *current* one looking for paragraphs with no
close match in the prior, so Loom could see a risk appear and never see one
resolve. Every finding the diff could produce was negative, which made the
verdict negative by construction: the stance had no route to improvement except
the company saying something reassuring in another section entirely.

Measured on Apple's two most recent annual reports, 39 paragraphs were added and
45 were removed. The removed half was simply invisible.

A withdrawn disclosure is not automatically good news — it can be a
consolidation or a rewrite — so the candidates go through the same language
judgement the additions already do. What changes here is only that they exist.
"""

from alembic import op

revision = "b4d2e7f1a903"
down_revision = "a1c7f3e9d248"
branch_labels = None
depends_on = None

_NEW_VALUE = "RESOLVED_RISK_FACTOR"


def upgrade() -> None:
    # Outside the migration's transaction. Postgres will not let a new enum
    # label be added and then used in the same transaction, and Alembic wraps
    # every migration in one.
    with op.get_context().autocommit_block():
        op.execute(
            f"ALTER TYPE signal_type ADD VALUE IF NOT EXISTS '{_NEW_VALUE}'"
        )


def downgrade() -> None:
    # Postgres cannot drop an enum label. Rebuilding the type would mean
    # rewriting every row that references it, and since the only way to reach
    # this downgrade is to abandon the bidirectional diff entirely, leaving an
    # unused label is the cheaper and safer outcome. Stated rather than hidden
    # behind a silent pass.
    pass
