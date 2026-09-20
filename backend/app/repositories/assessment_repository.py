"""The only module that queries the `event_assessments` table."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.event_assessment import EventAssessment


class AssessmentRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, **fields) -> EventAssessment | None:
        """Insert one assessment, or None if this filing was already assessed.

        The watcher re-reads a feed window that overlaps its own poll interval,
        so seeing a filing twice is the normal case rather than an error. The
        unique constraint decides; the caller just skips a None.
        """
        assessment = EventAssessment(**fields)
        self.db.add(assessment)
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            return None
        self.db.refresh(assessment)
        return assessment

    def exists(self, company_id: uuid.UUID, external_id: str) -> bool:
        stmt = select(EventAssessment.id).where(
            EventAssessment.company_id == company_id,
            EventAssessment.external_id == external_id,
        )
        return self.db.execute(stmt).first() is not None

    def seen_external_ids(self, since: datetime) -> set[str]:
        """Every filing already assessed since a cutoff.

        Loaded once per watcher cycle so the common case, a feed window full of
        filings already seen, costs one query rather than one per entry.
        """
        stmt = select(EventAssessment.external_id).where(EventAssessment.assessed_at >= since)
        return {row[0] for row in self.db.execute(stmt).all()}

    def recent(self, limit: int = 50, min_score: float | None = None) -> list[EventAssessment]:
        stmt = select(EventAssessment)
        if min_score is not None:
            stmt = stmt.where(EventAssessment.score >= min_score)
        stmt = stmt.order_by(EventAssessment.occurred_at.desc()).limit(limit)
        return list(self.db.execute(stmt).scalars().all())

    def for_company(self, company_id: uuid.UUID, limit: int = 50) -> list[EventAssessment]:
        stmt = (
            select(EventAssessment)
            .where(EventAssessment.company_id == company_id)
            .order_by(EventAssessment.occurred_at.desc())
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())
