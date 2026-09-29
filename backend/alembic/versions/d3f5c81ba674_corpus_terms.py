"""A boilerplate baseline measured from filing text rather than from findings.

The genre norms in engine/disclosure.py are measured from Loom's own extracted
findings, so the instrument calibrates against itself: a bias in extraction
becomes a bias in the expectation it is scored against, and nothing detects it
because the two agree by construction.

This table holds document frequency per term over the raw corpus — how many
distinct companies use each word in the sections Loom compares. It needs no
model, and it is the one signal that can say "this sentence appears in no other
filing", which is a claim about the corpus rather than about the extractor.
"""

import sqlalchemy as sa
from alembic import op

revision = "d3f5c81ba674"
down_revision = "c7a1b93d5e02"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "corpus_terms",
        sa.Column("term", sa.String(length=64), primary_key=True),
        sa.Column("companies", sa.Integer(), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    # The load reads the whole table and filters by frequency, so the index that
    # matters is on the count rather than on the term.
    op.create_index("ix_corpus_terms_companies", "corpus_terms", ["companies"])


def downgrade() -> None:
    op.drop_index("ix_corpus_terms_companies", table_name="corpus_terms")
    op.drop_table("corpus_terms")
