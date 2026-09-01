from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import settings

# Ingestion runs `settings.ingest_max_workers` tickers at once and each worker
# holds its own session for the length of a ticker, which can be minutes on a
# first backfill. Sized so a full ingest batch cannot starve the API of
# connections: the workers, the requests being served alongside them, and the
# scheduler's own session all have to fit at once.
_POOL_SIZE = max(5, settings.ingest_max_workers + 4)

engine = create_engine(
    settings.database_url,
    pool_pre_ping=True,
    pool_size=_POOL_SIZE,
    max_overflow=_POOL_SIZE,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency: yields one session per request, always closed."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
