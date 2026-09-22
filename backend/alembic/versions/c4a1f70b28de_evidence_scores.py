"""Persist the statistical evidence score on each finding.

The engine in engine/statistics/ ran in shadow: it computed, logged, and threw
the result away, so nothing downstream could use it and no conclusion it
reached was auditable after the log rotated. These two columns give the score a
home.

`evidence_rate` is how often this company produces findings of this severity,
shrunk toward the cross-company rate; `evidence_sample_size` is how many prior
findings that estimate rests on. Both nullable, because a score exists only
where a company has enough assessed history to form a baseline, and the
difference between "ordinary" and "not enough history to say" must stay
visible.

Deliberately no `is_anomalous` column. That verdict is the rate compared
against one threshold (statistics/engine.py ANOMALY_RATE), and storing it would
mean every threshold change needed a data migration to stay truthful.

Revision ID: c4a1f70b28de
Revises: b6d2a8e73f91
"""

import sqlalchemy as sa
from alembic import op

revision = "c4a1f70b28de"
down_revision = "b6d2a8e73f91"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("signals", sa.Column("evidence_rate", sa.Float(), nullable=True))
    op.add_column("signals", sa.Column("evidence_sample_size", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("signals", "evidence_sample_size")
    op.drop_column("signals", "evidence_rate")
