"""How many companies use each term, across the whole filing corpus.

The baseline this table exists to provide is independent of Loom's own
extraction, which the genre norms in engine/disclosure.py are not: those are
measured from findings the model produced, so a bias in the extractor becomes a
bias in the expectation it is scored against, self-consistently and invisibly.

This is measured from raw filing text instead. A term appearing in 200 companies'
risk sections is the industry's vocabulary; one appearing in three is specific to
those three. Nothing in that judgement passes through a model.

Document frequency is counted over *companies*, not documents. A filer that
repeats a phrase in four consecutive annual reports has told us one thing about
the phrase, not four, and counting documents would make a verbose company's
private vocabulary look like the industry's.
"""

from datetime import datetime

from sqlalchemy import DateTime, Integer, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CorpusTerm(Base):
    """One term and the number of distinct companies whose filings contain it."""

    __tablename__ = "corpus_terms"

    term: Mapped[str] = mapped_column(String(64), primary_key=True)
    companies: Mapped[int] = mapped_column(Integer, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
