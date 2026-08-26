"""Ingestion/engine job entry points, callable from a scheduler, a FastAPI
background task, or a CLI script. Each job opens its own DB session,
since it may run after the request that triggered it has already
finished (and its request-scoped session closed).

`run_initial_ingest` is called as a FastAPI BackgroundTask right after a
brand-new ticker is added to a watchlist, so filings start appearing without
any manual CLI step. `run_scheduled_refresh` (Phase 3) runs the same
`ingest_all` + analysis over every watchlist ticker on a timer. One code path,
several triggers, which is why adding the scheduler needed no new ingestion
logic at all.
"""

import logging

from app.db.session import SessionLocal
from app.engine.pipeline import analyze_company_recent
from app.engine.llm_client import LLMUnavailableError
from app.ingestion.registry import ingest_all
from app.repositories.watchlist_repository import WatchlistRepository

logger = logging.getLogger(__name__)


def run_initial_ingest(ticker: str) -> None:
    """Ingest a newly added ticker, then analyse its recent filings.

    Analysis runs in the same task so a new ticker arrives with signals
    already attached rather than as an empty timeline. It is deliberately
    after ingestion and separately guarded: a missing API key must leave the
    ingested filings in place, not fail the whole job.
    """
    db = SessionLocal()
    try:
        results = ingest_all(ticker, db)
        logger.info("Initial ingest for %s complete: %s", ticker, results)
        run_analysis(ticker)
    except Exception:
        # A background task has no request to report failure to, log it
        # rather than let it vanish silently or crash the worker.
        logger.exception("Initial ingest failed for %s", ticker)
    finally:
        db.close()


def run_analysis(ticker: str, force: bool = False) -> None:
    """Analyse a ticker's recent filings. Safe to call when no API key is set."""
    db = SessionLocal()
    try:
        count = analyze_company_recent(ticker, db, force=force)
        logger.info("Analysis for %s complete: %d signals", ticker, count)
    except LLMUnavailableError as exc:
        # Not an error worth a stack trace: the app is simply not configured
        # for analysis yet, and ingestion continues to work without it.
        logger.warning("Skipping analysis for %s: %s", ticker, exc)
    except Exception:
        logger.exception("Analysis failed for %s", ticker)
    finally:
        db.close()


def run_scheduled_refresh() -> None:
    """Re-ingest and re-analyse every ticker on the watchlist.

    Each ticker is isolated: one company's source going down, or its analysis
    hitting a provider limit, must not stop the rest of the watchlist from
    refreshing. Quota exhaustion is the one case that stops the pass early,
    because it will apply equally to every remaining ticker and continuing
    would just log the same failure N more times.
    """
    db = SessionLocal()
    try:
        watchlist = WatchlistRepository(db).get_or_create_default()
        companies = WatchlistRepository(db).list_companies(watchlist.id)
    except Exception:
        logger.exception("Scheduled refresh could not read the watchlist.")
        db.close()
        return

    tickers = [company.ticker for company in companies]
    db.close()

    if not tickers:
        logger.info("Scheduled refresh: watchlist is empty, nothing to do.")
        return

    logger.info("Scheduled refresh starting for %d tickers.", len(tickers))
    for ticker in tickers:
        session = SessionLocal()
        try:
            results = ingest_all(ticker, session)
            logger.info("Scheduled ingest for %s: %s", ticker, results)
        except Exception:
            logger.exception("Scheduled ingest failed for %s", ticker)
        finally:
            session.close()

        try:
            analysis_session = SessionLocal()
            try:
                count = analyze_company_recent(ticker, analysis_session)
                logger.info("Scheduled analysis for %s: %d signals", ticker, count)
            finally:
                analysis_session.close()
        except LLMUnavailableError as exc:
            logger.warning("Scheduled refresh stopping early, LLM unavailable: %s", exc)
            return
        except Exception:
            logger.exception("Scheduled analysis failed for %s", ticker)

    logger.info("Scheduled refresh complete.")
