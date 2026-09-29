"""Every paragraph-level judgement the model made, including the ones it rejected.

This table exists because Loom was generating the labelled dataset needed to
train its own replacement and throwing half of it away.

The risk comparison works in two stages. `diffing.py` finds paragraphs that
differ between two filings deterministically, then the model decides which of
those are substantive. Only the substantive ones became signals — the rejections
were dropped on the floor by `if not item.is_substantive: continue`, and nothing
else persisted the model's raw output: `document_analyses` records status and a
count, not a payload.

The consequence was that the corpus held 262 positive examples and zero
negatives, so no classifier could be trained on it at any price. The model had
already done the work and the quota had already been spent; only the answer was
discarded.

Both classes are stored here rather than joining positives back from `signals`,
so a training set is one table and one query. Nothing in the engine reads this
table — it is a record, not an input, and keeping it out of the read path is
deliberate: a label store that fed the verdict would make the verdict partly a
function of its own history.
"""

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class AssessmentLabel(Base):
    """One paragraph, and what the model decided about it."""

    __tablename__ = "assessment_labels"
    __table_args__ = (
        # A re-analysis of the same filing must not duplicate its labels. The
        # hash is over the quote rather than the quote itself, because a risk
        # paragraph runs to a few thousand characters and Postgres will not index
        # that.
        UniqueConstraint(
            "document_id", "kind", "quote_hash", name="uq_assessment_labels_document_quote"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id"), nullable=False, index=True
    )
    # The filing being analysed. For a resolved risk the quote's text lives in
    # `compared_document_id` instead, which is the whole reason both are stored.
    document_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_documents.id"), nullable=False, index=True
    )
    compared_document_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("raw_documents.id"), nullable=True
    )

    # Which question the model was answering. Kept as a string rather than an
    # enum: a new comparison kind should not need a migration to start
    # collecting labels for, and nothing branches on this in the engine.
    kind: Mapped[str] = mapped_column(String, nullable=False, index=True)
    section: Mapped[str | None] = mapped_column(String, nullable=True)

    quote: Mapped[str] = mapped_column(Text, nullable=False)
    quote_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    # The model's verdict on this paragraph. True means it became a finding,
    # false means it was judged a rewording, a consolidation or boilerplate.
    accepted: Mapped[bool] = mapped_column(Boolean, nullable=False, index=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Which model and prompt produced the judgement. Without these the dataset
    # silently mixes labellers of different quality, and a classifier trained on
    # the mixture inherits the average rather than the best.
    model: Mapped[str | None] = mapped_column(String, nullable=True)
    prompt_version: Mapped[str | None] = mapped_column(String, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
