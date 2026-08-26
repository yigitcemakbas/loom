"""The only module that queries `structured_facts`.

Mirrors DocumentRepository's role for the other storage shape. Note what is
absent: no BlobStore, because a fact has no body to store, its value *is* the
row. That asymmetry is the point of having two tables rather than forcing
numeric data through a text pipeline.
"""

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.structured_fact import FactType, StructuredFact


class FactRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(
        self,
        *,
        company_id: uuid.UUID,
        fact_type: FactType,
        source_name: str,
        as_of_date: date,
        content_hash: str,
        value: float | None = None,
        unit: str | None = None,
        source_url: str | None = None,
        attributes: dict | None = None,
    ) -> StructuredFact | None:
        """Returns None instead of raising when this fact is already stored,
        the same dedupe contract DocumentRepository.create offers."""
        fact = StructuredFact(
            company_id=company_id,
            fact_type=fact_type,
            source_name=source_name,
            as_of_date=as_of_date,
            value=value,
            unit=unit,
            source_url=source_url,
            attributes=attributes or {},
            content_hash=content_hash,
        )
        self.db.add(fact)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            return None
        self.db.refresh(fact)
        return fact

    def list_for_company(
        self,
        company_id: uuid.UUID,
        *,
        fact_type: FactType | None = None,
        since: date | None = None,
        limit: int = 200,
    ) -> list[StructuredFact]:
        """Most recent first, which is how every reader wants them."""
        stmt = select(StructuredFact).where(StructuredFact.company_id == company_id)
        if fact_type is not None:
            stmt = stmt.where(StructuredFact.fact_type == fact_type)
        if since is not None:
            stmt = stmt.where(StructuredFact.as_of_date >= since)
        stmt = stmt.order_by(StructuredFact.as_of_date.desc()).limit(limit)
        return list(self.db.execute(stmt).scalars().all())

    def earnings_events(self, company_id: uuid.UUID, limit: int = 40) -> list[StructuredFact]:
        """Earnings dates for one company, oldest first.

        Includes future dates, which is the point: this is the only fact type
        whose `as_of_date` is routinely ahead of today. Estimate revisions are
        stored as separate rows, so callers wanting current consensus take the
        most recently fetched row per date (see `latest_per_date`).
        """
        stmt = (
            select(StructuredFact)
            .where(
                StructuredFact.company_id == company_id,
                StructuredFact.fact_type == FactType.EARNINGS_EVENT,
            )
            .order_by(StructuredFact.as_of_date.desc())
            .limit(limit)
        )
        return list(reversed(self.db.execute(stmt).scalars().all()))

    @staticmethod
    def latest_per_date(events: list[StructuredFact]) -> list[StructuredFact]:
        """Collapse estimate revisions to the newest row for each date."""
        newest: dict = {}
        for event in events:
            existing = newest.get(event.as_of_date)
            if existing is None or event.fetched_at > existing.fetched_at:
                newest[event.as_of_date] = event
        return [newest[k] for k in sorted(newest)]

    def series(
        self, company_id: uuid.UUID, fact_type: FactType, *, limit: int = 60
    ) -> list[StructuredFact]:
        """One fact type over time, oldest first, for trend display and for the
        threshold rules that compare a reading against the prior one."""
        stmt = (
            select(StructuredFact)
            .where(
                StructuredFact.company_id == company_id,
                StructuredFact.fact_type == fact_type,
            )
            .order_by(StructuredFact.as_of_date.desc())
            .limit(limit)
        )
        return list(reversed(self.db.execute(stmt).scalars().all()))
