"""Shared FastAPI dependencies — DB session + repository factories.

Routes depend on these, never construct a Session or repository inline,
so swapping how a repository is built (e.g. adding caching later) touches
one place.
"""

from typing import Annotated

from fastapi import Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.repositories.company_repository import CompanyRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.watchlist_repository import WatchlistRepository

DbSession = Annotated[Session, Depends(get_db)]


def get_company_repository(db: DbSession) -> CompanyRepository:
    return CompanyRepository(db)


def get_watchlist_repository(db: DbSession) -> WatchlistRepository:
    return WatchlistRepository(db)


def get_document_repository(db: DbSession) -> DocumentRepository:
    return DocumentRepository(db)


CompanyRepo = Annotated[CompanyRepository, Depends(get_company_repository)]
WatchlistRepo = Annotated[WatchlistRepository, Depends(get_watchlist_repository)]
DocumentRepo = Annotated[DocumentRepository, Depends(get_document_repository)]
