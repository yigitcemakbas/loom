"""The only module that queries the `company_priors` table."""

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.prior import CompanyPrior


class PriorRepository:
    def __init__(self, db: Session):
        self.db = db

    def create(self, **fields) -> CompanyPrior:
        prior = CompanyPrior(**fields)
        self.db.add(prior)
        self.db.commit()
        self.db.refresh(prior)
        return prior

    def latest_for(self, company_id: uuid.UUID) -> CompanyPrior | None:
        """The prior the fast path reads.

        Rows are appended rather than replaced, so this is the one query that
        matters at event time and it is a single indexed lookup by design.
        """
        stmt = (
            select(CompanyPrior)
            .where(CompanyPrior.company_id == company_id)
            .order_by(CompanyPrior.generated_at.desc())
            .limit(1)
        )
        return self.db.execute(stmt).scalars().first()

    def history_for(self, company_id: uuid.UUID, limit: int = 20) -> list[CompanyPrior]:
        stmt = (
            select(CompanyPrior)
            .where(CompanyPrior.company_id == company_id)
            .order_by(CompanyPrior.generated_at.desc())
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    def stale_before(self, cutoff: datetime, limit: int = 100) -> list[uuid.UUID]:
        """Companies whose newest prior predates `cutoff`, oldest first.

        Drives refresh scheduling: a prior is a perishable thing, and one built
        before the last earnings call is worse than none because it is
        confidently out of date.
        """
        newest = (
            select(
                CompanyPrior.company_id,
                CompanyPrior.generated_at,
            )
            .order_by(CompanyPrior.company_id, CompanyPrior.generated_at.desc())
            .distinct(CompanyPrior.company_id)
            .subquery()
        )
        stmt = (
            select(newest.c.company_id)
            .where(newest.c.generated_at < cutoff)
            .limit(limit)
        )
        return [row[0] for row in self.db.execute(stmt).all()]
