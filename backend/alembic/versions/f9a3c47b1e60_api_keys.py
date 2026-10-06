"""A durable credential for programmatic access.

Loom's only credential was a browser session, issued by emailing a six-digit
code. That is right for a person and useless for an agent, which has no inbox
and needs a secret it can hold in configuration. The evidence API exists because
agents reading evidence were the best-performing arm of the reader benchmark,
and until now there was no way to give one a credential.

Stored as a sha256 digest, like user_sessions, so the table never holds a usable
secret. Revoked rather than deleted, so a key that turns up in a log afterwards
is still identifiable.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "f9a3c47b1e60"
down_revision = "e8b402cd91f7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_keys",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "user_id", postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("name", sa.String(80), nullable=False),
        # Leading characters of the plaintext, in clear. Enough to match a key
        # in a config file against a row here, far too little to authenticate.
        sa.Column("prefix", sa.String(16), nullable=False),
        sa.Column("key_hash", sa.String, nullable=False, unique=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_api_keys_user_id", "api_keys", ["user_id"])
    op.create_index("ix_api_keys_prefix", "api_keys", ["prefix"])
    # The lookup path on every authenticated API request.
    op.create_index("ix_api_keys_key_hash", "api_keys", ["key_hash"], unique=True)


def downgrade() -> None:
    op.drop_index("ix_api_keys_key_hash", table_name="api_keys")
    op.drop_index("ix_api_keys_prefix", table_name="api_keys")
    op.drop_index("ix_api_keys_user_id", table_name="api_keys")
    op.drop_table("api_keys")
