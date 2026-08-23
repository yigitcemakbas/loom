import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.watchlist import Watchlist, WatchlistItem
from app.schemas.watchlist import WatchlistCreate


class WatchlistRepository:
    def __init__(self, db: Session):
        self.db = db

    def get_by_id(self, watchlist_id: uuid.UUID) -> Watchlist | None:
        return self.db.get(Watchlist, watchlist_id)

    def list_all(self) -> list[Watchlist]:
        return list(self.db.execute(select(Watchlist)).scalars().all())

    def get_or_create_default(self) -> Watchlist:
        existing = self.db.execute(select(Watchlist).limit(1)).scalar_one_or_none()
        if existing:
            return existing
        return self.create(WatchlistCreate())

    def create(self, data: WatchlistCreate) -> Watchlist:
        watchlist = Watchlist(name=data.name, owner_email=data.owner_email)
        self.db.add(watchlist)
        self.db.commit()
        self.db.refresh(watchlist)
        return watchlist

    def list_companies(self, watchlist_id: uuid.UUID) -> list[Company]:
        stmt = (
            select(Company)
            .join(WatchlistItem, WatchlistItem.company_id == Company.id)
            .where(WatchlistItem.watchlist_id == watchlist_id)
        )
        return list(self.db.execute(stmt).scalars().all())

    def add_company(self, watchlist_id: uuid.UUID, company_id: uuid.UUID) -> None:
        existing = self.db.get(WatchlistItem, (watchlist_id, company_id))
        if existing:
            return
        self.db.add(WatchlistItem(watchlist_id=watchlist_id, company_id=company_id))
        self.db.commit()

    def remove_company(self, watchlist_id: uuid.UUID, company_id: uuid.UUID) -> None:
        item = self.db.get(WatchlistItem, (watchlist_id, company_id))
        if item:
            self.db.delete(item)
            self.db.commit()
