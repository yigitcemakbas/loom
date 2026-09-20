"""The only module allowed to run SQL/ORM queries against `companies`.

Routes, scripts, and ingestion code call this, never a raw Session query, so storage details stay decoupled from business logic (see docs/plan.md
"Architecture Principles").
"""

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company import Company, CompanyTier
from app.schemas.company import CompanyCreate


class CompanyRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, company_id: uuid.UUID) -> Company | None:
        return self.db.get(Company, company_id)

    def get_by_ticker(self, ticker: str) -> Company | None:
        stmt = select(Company).where(Company.ticker == ticker.upper())
        return self.db.execute(stmt).scalar_one_or_none()

    def search(self, query: str, limit: int = 20) -> list[Company]:
        like = f"%{query}%"
        stmt = (
            select(Company)
            .where((Company.ticker.ilike(like)) | (Company.name.ilike(like)))
            .limit(limit)
        )
        return list(self.db.execute(stmt).scalars().all())

    def list_all(self) -> list[Company]:
        return list(self.db.execute(select(Company)).scalars().all())

    def create(self, data: CompanyCreate, tier: CompanyTier = CompanyTier.WIDE) -> Company:
        # Wide by default so that seeding a universe cannot accidentally commit
        # the project to a model call per filing for every name added.
        company = Company(
            ticker=data.ticker.upper(),
            name=data.name,
            cik=data.cik,
            sector=data.sector,
            exchange=data.exchange,
            tier=tier,
        )
        self.db.add(company)
        self.db.commit()
        self.db.refresh(company)
        return company

    def get_or_create(
        self, data: CompanyCreate, tier: CompanyTier = CompanyTier.WIDE
    ) -> Company:
        existing = self.get_by_ticker(data.ticker)
        if existing:
            return existing
        return self.create(data, tier=tier)

    def set_tier(self, ticker: str, tier: CompanyTier) -> Company | None:
        """Move a company between tiers. The project's main cost control."""
        company = self.get_by_ticker(ticker)
        if company is None:
            return None
        company.tier = tier
        self.db.commit()
        self.db.refresh(company)
        return company

    def list_by_tier(self, tier: CompanyTier) -> list[Company]:
        return list(
            self.db.execute(select(Company).where(Company.tier == tier)).scalars().all()
        )
