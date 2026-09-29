"""Keep the paragraph judgements the model rejected, not only the ones it accepted.

The risk comparison decides, per paragraph, whether a change is substantive. Only
the substantive ones became signals; the rejections were dropped by an early
`continue` and nothing else stored the model's raw output, since
`document_analyses` records a status and a count rather than a payload.

So the corpus held 262 positive examples and zero negatives, and no classifier
could be trained on it at any price — while every drip run generated both classes
and spent the quota to do it. This table keeps both.

Deliberately not read by the engine. It is a record for training and audit, and
wiring it into the read path would make the verdict partly a function of its own
history.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "c7a1b93d5e02"
down_revision = "b4d2e7f1a903"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "assessment_labels",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "company_id",
            UUID(as_uuid=True),
            sa.ForeignKey("companies.id"),
            nullable=False,
        ),
        sa.Column(
            "document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("raw_documents.id"),
            nullable=False,
        ),
        sa.Column(
            "compared_document_id",
            UUID(as_uuid=True),
            sa.ForeignKey("raw_documents.id"),
            nullable=True,
        ),
        sa.Column("kind", sa.String(), nullable=False),
        sa.Column("section", sa.String(), nullable=True),
        sa.Column("quote", sa.Text(), nullable=False),
        # Hashed because a risk paragraph runs to a few thousand characters and
        # Postgres will not index that, and the dedupe key has to be indexable.
        sa.Column("quote_hash", sa.String(length=64), nullable=False),
        sa.Column("accepted", sa.Boolean(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=True),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column("prompt_version", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "document_id",
            "kind",
            "quote_hash",
            name="uq_assessment_labels_document_quote",
        ),
    )
    op.create_index(
        "ix_assessment_labels_company_id", "assessment_labels", ["company_id"]
    )
    op.create_index(
        "ix_assessment_labels_document_id", "assessment_labels", ["document_id"]
    )
    op.create_index("ix_assessment_labels_kind", "assessment_labels", ["kind"])
    # The index that matters for the only query this table exists to serve:
    # assembling a balanced training set.
    op.create_index("ix_assessment_labels_accepted", "assessment_labels", ["accepted"])


def downgrade() -> None:
    op.drop_index("ix_assessment_labels_accepted", table_name="assessment_labels")
    op.drop_index("ix_assessment_labels_kind", table_name="assessment_labels")
    op.drop_index("ix_assessment_labels_document_id", table_name="assessment_labels")
    op.drop_index("ix_assessment_labels_company_id", table_name="assessment_labels")
    op.drop_table("assessment_labels")
