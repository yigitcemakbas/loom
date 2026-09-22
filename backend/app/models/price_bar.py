"""Stored daily closes, the series Loom previously fetched and threw away.

Price history existed here only as a chart: fetched on demand from an
undocumented endpoint, cached for sixty seconds, never written down. That was
the right call for drawing a line and the wrong one for everything else, and it
blocked three things at once. Valuation is a ratio between a company's figures
and its price, so without a stored price Loom could rank quality and never say
whether the quality was already paid for. Momentum is a function of past prices
by definition. And the evaluation harness could only measure findings whose
prices the provider happened to still serve, silently dropping the rest.

Daily bars only. Intraday candles would add hundreds of thousands of rows that
go stale immediately and answer no question the engine asks; a daily close is
what valuation, momentum and event studies are all defined on.

Two closes are kept because they answer different questions. `close` is what
the share actually traded at, which is the number that pairs with a share count
to give a market capitalisation. `adjusted_close` is restated for splits and
dividends, which is the only honest basis for measuring a return across time.
Using either one for both jobs produces an error that looks like a result.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import Date, DateTime, Float, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PriceBar(Base):
    __tablename__ = "price_bars"
    __table_args__ = (
        UniqueConstraint("company_id", "session_date", name="uq_price_bars_session"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The trading session this close belongs to. A date rather than a timestamp:
    # the engine reasons in sessions, and a timezone on a daily close invites
    # off-by-one errors at exactly the boundary where lookahead bias lives.
    session_date: Mapped[date] = mapped_column(Date, nullable=False, index=True)

    close: Mapped[float] = mapped_column(Float, nullable=False)
    adjusted_close: Mapped[float] = mapped_column(Float, nullable=False)
    volume: Mapped[float | None] = mapped_column(Float, nullable=True)

    source: Mapped[str] = mapped_column(String, nullable=False, default="yahoo")
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
