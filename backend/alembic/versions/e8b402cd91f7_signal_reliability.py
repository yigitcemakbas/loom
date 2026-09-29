"""Record what each kind of finding has actually been worth.

engine/evaluation.py has been able to measure whether findings predicted anything
for most of this project's life, and gated nothing: priority.py ranks findings by
a hand-written table of type weights that no outcome has ever checked.

This table closes that loop. It is designed to stay inert until it has earned the
right not to be — a multiplier is 1.0 unless the type clears a sample floor and a
t-statistic bar, and with 695 findings across eight types every one is below the
floor today. That is the correct behaviour, not a limitation: fitting eight
weights to a few dozen noisy observations each is the mistake quant/relevance.py
refuses by name.
"""

import sqlalchemy as sa
from alembic import op

revision = "e8b402cd91f7"
down_revision = "d3f5c81ba674"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "signal_reliability",
        sa.Column("signal_type", sa.String(length=64), primary_key=True),
        sa.Column("horizon_sessions", sa.Integer(), primary_key=True),
        sa.Column("observations", sa.Integer(), nullable=False),
        sa.Column("hit_rate", sa.Float(), nullable=True),
        sa.Column("baseline_hit_rate", sa.Float(), nullable=True),
        sa.Column("spread", sa.Float(), nullable=True),
        sa.Column("t_statistic", sa.Float(), nullable=True),
        sa.Column("trusted", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("multiplier", sa.Float(), nullable=False, server_default="1.0"),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    # The read path wants only the rows allowed to affect ranking.
    op.create_index(
        "ix_signal_reliability_trusted", "signal_reliability",
        ["horizon_sessions", "trusted"],
    )


def downgrade() -> None:
    op.drop_index("ix_signal_reliability_trusted", table_name="signal_reliability")
    op.drop_table("signal_reliability")
