"""Shared FastAPI dependencies, DB session + repository factories.

Routes depend on these, never construct a Session or repository inline,
so swapping how a repository is built (e.g. adding caching later) touches
one place.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories.brief_repository import BriefRepository
from app.repositories.company_repository import CompanyRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.fact_repository import FactRepository
from app.repositories.search_repository import SearchRepository
from app.repositories.signal_repository import SignalRepository
from app.repositories.usage_repository import UsageRepository
from app.repositories.watchlist_repository import WatchlistRepository

DbSession = Annotated[Session, Depends(get_db)]


def get_company_repository(db: DbSession) -> CompanyRepository:
    return CompanyRepository(db)


def get_watchlist_repository(db: DbSession) -> WatchlistRepository:
    return WatchlistRepository(db)


def get_document_repository(db: DbSession) -> DocumentRepository:
    return DocumentRepository(db)


def get_signal_repository(db: DbSession) -> SignalRepository:
    return SignalRepository(db)


def get_usage_repository(db: DbSession) -> UsageRepository:
    return UsageRepository(db)


def get_search_repository(db: DbSession) -> SearchRepository:
    return SearchRepository(db)


def get_fact_repository(db: DbSession) -> FactRepository:
    return FactRepository(db)


def get_brief_repository(db: DbSession) -> BriefRepository:
    return BriefRepository(db)


CompanyRepo = Annotated[CompanyRepository, Depends(get_company_repository)]
WatchlistRepo = Annotated[WatchlistRepository, Depends(get_watchlist_repository)]
DocumentRepo = Annotated[DocumentRepository, Depends(get_document_repository)]
SignalRepo = Annotated[SignalRepository, Depends(get_signal_repository)]
UsageRepo = Annotated[UsageRepository, Depends(get_usage_repository)]
SearchRepo = Annotated[SearchRepository, Depends(get_search_repository)]
FactRepo = Annotated[FactRepository, Depends(get_fact_repository)]
BriefRepo = Annotated[BriefRepository, Depends(get_brief_repository)]
