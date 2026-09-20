import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CompanyTier(str, enum.Enum):
    """How much attention a company gets, and therefore what it costs.

    The split exists because the two things this project wants are in direct
    tension. Cross-sectional work (ranking a name against its peers, ranking
    anything at all) needs hundreds of companies; language analysis of filings
    needs a model call per document, and a model call is the one resource here
    that does not scale for free. Applying the expensive path to a thousand
    names is arithmetically out of reach on a free tier, and applying only the
    cheap path to eleven names produces no ranking worth having.

    So neither is applied universally. A large WIDE tier carries the numeric
    sources, which cost one HTTP request per company per day and make
    cross-sectional statistics possible. A small FOCUS tier additionally reads
    the documents, which is where the expense and the depth both live.

    Moving a company between tiers is the product's main cost control, and it
    is a single field rather than a separate pipeline precisely so that it can
    be changed without redeploying anything.
    """

    # Filings, transcripts, news, model analysis, and a standing prior.
    FOCUS = "focus"
    # Numeric sources only: prices, insider transactions, short interest,
    # earnings dates and consensus. No documents, no model calls.
    WIDE = "wide"


class Company(Base):
    __tablename__ = "companies"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    ticker: Mapped[str] = mapped_column(String, unique=True, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    cik: Mapped[str | None] = mapped_column(String, nullable=True)  # SEC EDGAR CIK
    sector: Mapped[str | None] = mapped_column(String, nullable=True)
    exchange: Mapped[str | None] = mapped_column(String, nullable=True)
    # Defaults to WIDE so that bulk-seeding a universe is cheap by default and
    # expense is something you opt into per company, rather than something a
    # thousand-row insert quietly commits you to.
    tier: Mapped[CompanyTier] = mapped_column(
        SAEnum(CompanyTier, name="company_tier"),
        nullable=False,
        server_default=CompanyTier.WIDE.value,
        index=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<Company {self.ticker}>"
