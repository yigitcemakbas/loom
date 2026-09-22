"""Mark accounts that can see the instance rather than only their own data.

Admin here is oversight, not capability. Running the engine, adding companies
and reading everything Loom produces are the product, and every account can do
them; this flag grants sight of other accounts and of engine internals, which
are the two things somebody should not see by default on an instance shared
with anybody else.

Who gets it comes from configuration rather than from this table. A rule like
"the first account is admin" quietly hands the instance to whoever registers
first after a database reset, which is the wrong default for something that
can be redeployed.

Revision ID: c5e1b7f2a380
Revises: b3f8a21d9e64
"""

import sqlalchemy as sa
from alembic import op

revision = "c5e1b7f2a380"
down_revision = "b3f8a21d9e64"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("is_admin", sa.Boolean(), nullable=False, server_default=sa.false()),
    )


def downgrade() -> None:
    op.drop_column("users", "is_admin")
