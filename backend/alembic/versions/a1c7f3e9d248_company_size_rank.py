"""Record each company's size rank from SEC's own directory.

SEC's filer directory is published in market-capitalisation order: it opens
NVDA, AAPL, GOOGL, MSFT and is into micro-caps by row three thousand. Storing
the position costs nothing at seed time and buys a size proxy that Loom
otherwise has to compute from a share count it may not hold.

It exists because of a bug that only appears at scale. A sector peer group was
selected with `limit(25)` and no ordering, which returns whichever twenty-five
rows the database hands back. At a hundred and thirty companies that was
nearly the whole sector and the arbitrariness did not show. At a thousand,
Technology has two hundred and thirty members and an unordered twenty-five of
them is a different, irreproducible peer group on every query.

Nullable, because a company added by hand or resolved from a ticker the user
typed has no position in that directory, and inventing one would be worse than
admitting there isn't one.

Revision ID: a1c7f3e9d248
Revises: e21f8a47c3b9
"""

import sqlalchemy as sa
from alembic import op

revision = "a1c7f3e9d248"
down_revision = "e21f8a47c3b9"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("companies", sa.Column("sec_rank", sa.Integer(), nullable=True))
    # Indexed because every sector peer lookup orders by it.
    op.create_index("ix_companies_sec_rank", "companies", ["sec_rank"])


def downgrade() -> None:
    op.drop_index("ix_companies_sec_rank", table_name="companies")
    op.drop_column("companies", "sec_rank")
