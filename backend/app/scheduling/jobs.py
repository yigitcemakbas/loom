"""Ingestion/engine job entry points, callable from a scheduler, a FastAPI
background task, or a CLI script. Each job opens its own DB session,
since it may run after the request that triggered it has already
finished (and its request-scoped session closed).

Phase 1: `run_initial_ingest` is called as a FastAPI BackgroundTask right
after a brand-new ticker is added to a watchlist, so filings start
appearing without any manual CLI step. Phase 3 adds a periodic
APScheduler job here that calls the same `ingest_all` on every watchlist
ticker on a schedule — one code path, multiple triggers.
"""

import logging

from app.db.session import SessionLocal
from app.ingestion.registry import ingest_all

logger = logging.getLogger(__name__)


def run_initial_ingest(ticker: str) -> None:
    db = SessionLocal()
    try:
        results = ingest_all(ticker, db)
        logger.info("Initial ingest for %s complete: %s", ticker, results)
    except Exception:
        # A background task has no request to report failure to — log it
        # rather than let it vanish silently or crash the worker.
        logger.exception("Initial ingest failed for %s", ticker)
    finally:
        db.close()
