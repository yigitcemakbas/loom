import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CompanyPrior(Base):
    """What Loom already believes about a company, worked out before anything happens.

    This table exists to solve a timing problem that no amount of optimisation
    fixes. The engine's expensive step is reading documents with a language
    model, which is paced in seconds per call. The moment a company reports,
    the useful window is minutes. Thinking *after* the event arrives therefore
    cannot work: by the time a verdict exists the move has happened.

    So the order is inverted. The slow reading runs continuously and in
    advance, and what it produces is not a verdict but a set of expectations:
    the specific things that would matter about this company if they occurred,
    what the market already expects, and what it has already reacted to. When
    an event does land, scoring it against those expectations is arithmetic
    (`engine/reaction.py`) and takes microseconds, because the thinking was
    already done.

    The analogy is an analyst who has read the last four filings and knows
    which line to look at first, versus one who starts reading when the press
    release drops. The second one is never going to be fast enough, however
    quickly they read.

    Rows are kept rather than overwritten so that a reaction can be audited
    against the prior that actually produced it, rather than against whatever
    Loom believes today.
    """

    __tablename__ = "company_priors"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # One plain sentence: what to watch on this company right now.
    summary: Mapped[str] = mapped_column(Text, nullable=False)

    # The heart of the table. Each entry is something that would move the
    # stock if it happened, written concretely enough that a deterministic
    # matcher can recognise it in an event without asking a model:
    # [{topic, keywords: [...], watch_for, direction_if_confirmed,
    #   severity, why_it_matters, evidence_quote, signal_ids: [...]}]
    watch_items: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # What the market currently expects, so a surprise can be measured against
    # something rather than asserted. {eps_estimate, revenue_estimate,
    # next_report_date, quarter_label, guidance_notes}
    expectations: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # How the market is positioned going in, computed arithmetically rather
    # than read: {days_to_cover, short_crowded, insider_net_usd,
    # position_in_52w_range, change_1m, change_3m}. This is what decides
    # whether a surprise lands on a crowded short or an indifferent one.
    positioning: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)

    # Things the market has demonstrably already reacted to, so the engine does
    # not treat old news as new. [{topic, moved_percent, sessions, as_of}]
    already_priced: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)

    # How many findings and which sources the prior was built from, for the
    # same auditability reason briefs carry theirs.
    source_signal_count: Mapped[int] = mapped_column(nullable=False, default=0)

    prompt_version: Mapped[str] = mapped_column(String, nullable=False)
    engine_version: Mapped[str] = mapped_column(String, nullable=False)
    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<CompanyPrior {self.company_id} {len(self.watch_items or [])} items>"
