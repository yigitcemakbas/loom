import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class CompanyExposure(Base):
    """Company A is exposed to company B, measured by how often A's own filings name B.

    This is the edge that lets an event propagate. Loom's other sources all
    answer questions about one company in isolation, but the companies that
    move on NVIDIA's earnings are frequently not NVIDIA: a contract
    manufacturer, a memory supplier, a GPU cloud. Knowing who is downstream of
    an event is what turns a single filing into a read on several positions.

    **Direction matters and is easy to get backwards.** The edge points from
    the dependent to the hub. AMD's filings naming NVIDIA means AMD is exposed
    to NVIDIA, not the reverse; NVIDIA barely needs to mention AMD. Reversing
    it would propagate every event to exactly the wrong set of companies.

    The measure is deliberately crude. A count of filings in which one company
    names another is a proxy for exposure, not a measurement of it, and it is
    biased toward companies that file often. What justifies it is the
    separation it achieves in practice: across the tracked universe, semiconductor
    names mention NVIDIA in a hundred or more filings while unrelated large
    caps manage nought to six.
    """

    __tablename__ = "company_exposures"
    __table_args__ = (
        UniqueConstraint("dependent_company_id", "hub_company_id", name="uq_exposure_pair"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # The company that is affected when something happens to the hub.
    dependent_company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # The company whose events propagate.
    hub_company_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("companies.id", ondelete="CASCADE"), nullable=False, index=True
    )

    # Filings by the dependent that name the hub.
    mention_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    source: Mapped[str] = mapped_column(String, nullable=False, default="sec-edgar-fts")
    computed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"<CompanyExposure dependent={self.dependent_company_id} hub={self.hub_company_id}>"
