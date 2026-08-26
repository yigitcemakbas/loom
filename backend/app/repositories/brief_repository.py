"""The only module that queries `company_briefs`."""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.brief import CompanyBrief, Stance


class BriefRepository:
    def __init__(self, db: Session):
        self.db = db

    def latest_for(self, company_id: uuid.UUID) -> CompanyBrief | None:
        stmt = (
            select(CompanyBrief)
            .where(CompanyBrief.company_id == company_id)
            .order_by(CompanyBrief.generated_at.desc())
            .limit(1)
        )
        return self.db.execute(stmt).scalars().first()

    def history_for(self, company_id: uuid.UUID, limit: int = 20) -> list[CompanyBrief]:
        """Past reads, newest first, so a reader can see how the view evolved."""
        stmt = (
            select(CompanyBrief)
            .where(CompanyBrief.company_id == company_id)
            .order_by(CompanyBrief.generated_at.desc())
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    def create(
        self,
        *,
        company_id: uuid.UUID,
        stance: Stance,
        headline: str,
        confidence: float,
        drivers: list,
        what_changed: str | None,
        source_types: list,
        signal_count: int,
        evidence: dict,
        engine_version: str,
    ) -> CompanyBrief:
        brief = CompanyBrief(
            company_id=company_id,
            stance=stance,
            headline=headline,
            confidence=confidence,
            drivers=drivers,
            what_changed=what_changed,
            source_types=source_types,
            signal_count=signal_count,
            evidence=evidence,
            engine_version=engine_version,
        )
        self.db.add(brief)
        self.db.commit()
        self.db.refresh(brief)
        return brief
