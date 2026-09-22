"""Ingestion/engine job entry points, callable from a scheduler, a FastAPI
background task, or a CLI script. Each job opens its own DB session,
since it may run after the request that triggered it has already
finished (and its request-scoped session closed).

`run_initial_ingest` is called as a FastAPI BackgroundTask right after a
brand-new ticker is added to a watchlist, so filings start appearing without
any manual CLI step. `run_scheduled_refresh` (Phase 3) covers the whole
watchlist on a timer, ingesting tickers concurrently and then analysing them
one at a time. One set of adapters, several triggers, which is why adding the
scheduler needed no new ingestion logic at all.
"""

import logging
import time
from datetime import datetime, timezone

from app.config import settings
from app.db.session import SessionLocal
from app.engine.pipeline import analyze_company_recent
from app.engine.prior import build_prior
from app.engine.llm_client import LLMUnavailableError
from app.ingestion.registry import ingest_all, ingest_many
from app.models.company import CompanyTier
from app.repositories.company_repository import CompanyRepository
from app.repositories.prior_repository import PriorRepository
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

    The two phases are deliberately different shapes.

    Ingestion runs concurrently. It is pure IO against several unrelated hosts,
    and the shared limiter in ingestion/rate_limit.py keeps the per-host rate
    fixed regardless of worker count, so overlapping tickers costs nothing in
    politeness and saves the sum of everybody's waiting.

    Analysis stays sequential, and not by oversight. The LLM client paces its
    own calls to stay inside a free-tier quota, so parallel analysis would
    queue behind that same pacing and finish no sooner while making the quota
    accounting harder to reason about. Running it after all ingestion, rather
    than interleaved per ticker, also means a slow filing download no longer
    delays analysis of a company whose documents are already stored.

    Each ticker is isolated in both phases. Quota exhaustion is the one failure
    that stops the pass early, because it applies equally to every remaining
    ticker and continuing would only log the same failure N more times.
    """
    db = SessionLocal()
    try:
        watchlist = WatchlistRepository(db).get_or_create_default()
        companies = WatchlistRepository(db).list_companies(watchlist.id)
    except Exception:
        logger.exception("Scheduled refresh could not read the watchlist.")
        db.close()
        return

    # Ingestion covers every tier; analysis and priors cost model calls and
    # so are confined to the focus tier. This split is the whole point of
    # tiering: the universe can grow without the model bill growing with it.
    tickers = [company.ticker for company in companies]
    focus_tickers = [c.ticker for c in companies if c.tier == CompanyTier.FOCUS]
    # Priors, and therefore live coverage, extend to the watch tier. Document
    # analysis does not, which is the entire cost difference between them.
    prior_tickers = [
        c.ticker for c in companies
        if c.tier in (CompanyTier.FOCUS, CompanyTier.WATCH)
    ]
    db.close()

    if not tickers:
        logger.info("Scheduled refresh: watchlist is empty, nothing to do.")
        return

    logger.info(
        "Scheduled refresh starting for %d tickers (%d focus, %d with priors), %d at a time.",
        len(tickers),
        len(focus_tickers),
        len(prior_tickers),
        settings.ingest_max_workers,
    )

    started = time.monotonic()
    results = ingest_many(tickers)
    elapsed = time.monotonic() - started

    for result in results:
        if result.ok:
            logger.info("Scheduled ingest for %s: %s", result.ticker, result.counts)
        else:
            logger.warning("Scheduled ingest failed for %s: %s", result.ticker, result.error)

    new_items = sum(result.total_new for result in results)
    failed = [result.ticker for result in results if not result.ok]
    logger.info(
        "Ingestion complete in %.1fs: %d new items across %d tickers, %d failed.",
        elapsed,
        new_items,
        len(results),
        len(failed),
    )

    for ticker in focus_tickers:
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

    _refresh_priors(prior_tickers)

    logger.info("Scheduled refresh complete.")


def _refresh_priors(tickers: list[str]) -> None:
    """Rebuild each focus company's standing prior after new material lands.

    Runs last on purpose. A prior is only as good as the findings behind it, so
    building one before the run's ingestion and analysis have landed would
    describe the company as it was yesterday. Guarded per ticker for the usual
    reason: a prior is preparation, and failing to prepare must not look like a
    failed refresh.
    """
    # Unarmed companies first. A quota-limited run stops partway through, so
    # the order decides what gets covered: refreshing a three-day-old prior
    # while another company has none at all is the wrong trade, because the
    # second one is scoring every filing zero in the meantime.
    db = SessionLocal()
    try:
        prior_repo = PriorRepository(db)
        company_repo = CompanyRepository(db)
        def _uncovered_first(ticker: str):
            company = company_repo.get_by_ticker(ticker)
            if company is None:
                return (2, datetime.max.replace(tzinfo=timezone.utc))
            latest = prior_repo.latest_for(company.id)
            return (
                1 if latest else 0,
                latest.generated_at if latest else datetime.min.replace(tzinfo=timezone.utc),
            )
        tickers = sorted(tickers, key=_uncovered_first)
    except Exception:
        logger.exception("Could not order priors by coverage; using the given order.")
    finally:
        db.close()

    for ticker in tickers:
        db = SessionLocal()
        try:
            prior = build_prior(ticker, db)
            if prior is not None:
                logger.info(
                    "Prior for %s: %d things to watch for.", ticker, len(prior.watch_items or [])
                )
        except LLMUnavailableError as exc:
            logger.warning("Prior generation stopping early, LLM unavailable: %s", exc)
            db.close()
            return
        except Exception:
            logger.exception("Prior generation failed for %s", ticker)
        finally:
            db.close()


# ---- the quantitative half -------------------------------------------
#
# These four are why Loom stops needing an operator. Every one of them is
# arithmetic over stored data: no model call, no quota, no cost, and no reason
# for a human to be the thing that decides when they run. Until now each was a
# script somebody had to remember, which made the whole quantitative layer a
# snapshot of whenever it was last run by hand.
#
# Ordered deliberately where they share a cadence: prices before factors,
# because valuation is a ratio to a price, and factors before briefs, because a
# brief reads the scores.


def run_price_refresh() -> None:
    """Pull yesterday's closes for the universe.

    Incremental by construction: sessions already stored are skipped, so the
    daily run fetches one bar per company rather than twenty years of them.
    """
    from app.ingestion.prices import get_price_source
    from app.models.price_bar import PriceBar
    from app.repositories.company_repository import CompanyRepository
    from sqlalchemy import select

    db = SessionLocal()
    try:
        source = get_price_source()
        written = 0
        for company in CompanyRepository(db).list_all():
            existing = set(db.execute(
                select(PriceBar.session_date).where(PriceBar.company_id == company.id)
            ).scalars())
            # A short window: the backfill established the history, and this
            # only has to close the gap since the last run.
            for bar in source.daily_history(company.ticker, years=1):
                if bar.session_date in existing:
                    continue
                db.add(PriceBar(
                    company_id=company.id, session_date=bar.session_date,
                    close=bar.close, adjusted_close=bar.adjusted_close, volume=bar.volume,
                ))
                written += 1
            db.commit()
        logger.info("Price refresh: %d new sessions stored.", written)
    except Exception:
        logger.exception("Price refresh failed.")
    finally:
        db.close()


def run_factor_scoring() -> None:
    """Rescore the universe on its filed financials.

    Weekly rather than daily: the fundamentals change only when somebody files,
    and the price-dependent factors move slowly enough that a daily rescore
    would burn cycles to redraw the same picture.
    """
    from app.engine.quant.runner import persist, score_universe

    db = SessionLocal()
    try:
        scores = score_universe(db)
        written = persist(db, scores)
        logger.info(
            "Factor scoring: %d companies, %d rows written.", len(scores.raw), written,
        )
    except Exception:
        logger.exception("Factor scoring failed.")
    finally:
        db.close()


def run_prior_replay() -> None:
    """Score filings that arrived since the last pass against standing priors.

    The watcher catches a filing only if it lands while Loom is running and the
    company is armed. This closes the gap for everything else, and it is the
    reason the fast path has a measurable record at all.
    """
    from datetime import timezone

    from app.engine.exposure import dependents_of
    from app.engine.reaction import MarketEvent, assess
    from app.models.document import RawDocument
    from app.repositories.assessment_repository import AssessmentRepository
    from app.repositories.company_repository import CompanyRepository
    from app.repositories.prior_repository import PriorRepository
    from app.storage.blob_store import get_blob_store
    from scripts.replay_priors import MAX_TEXT_CHARS, REPLAY_PREFIX
    from sqlalchemy import select

    db = SessionLocal()
    try:
        prior_repo = PriorRepository(db)
        assessment_repo = AssessmentRepository(db)
        blob_store = get_blob_store()
        written = notable = 0

        for company in CompanyRepository(db).list_all():
            prior = prior_repo.latest_for(company.id)
            if prior is None:
                continue
            # Strictly after the prior, always. A document the prior was built
            # from cannot test it.
            documents = db.execute(
                select(RawDocument)
                .where(RawDocument.company_id == company.id)
                .where(RawDocument.published_at > prior.generated_at)
            ).scalars()

            exposed = [
                {"ticker": e.ticker, "mention_count": e.mention_count}
                for e in dependents_of(db, company.id)
            ]
            for document in documents:
                try:
                    body = blob_store.get(document.blob_uri).decode("utf-8", errors="ignore")
                except Exception:
                    continue
                occurred = document.published_at
                if occurred is not None and occurred.tzinfo is None:
                    occurred = occurred.replace(tzinfo=timezone.utc)
                result = assess(
                    MarketEvent(
                        ticker=company.ticker,
                        kind="filing" if document.doc_subtype else "news",
                        occurred_at=occurred,
                        text=f"{document.doc_subtype or ''} {document.title or ''}\n{body[:MAX_TEXT_CHARS]}",
                        source_url=document.source_url,
                    ),
                    prior,
                )
                created = assessment_repo.create(
                    company_id=company.id, prior_id=prior.id,
                    external_id=f"{REPLAY_PREFIX}{document.id}",
                    kind="filing" if document.doc_subtype else "news",
                    form=document.doc_subtype, source_url=document.source_url,
                    score=result.score, direction=result.direction, headline=result.headline,
                    matches=[
                        {
                            "topic": m.topic, "direction": m.direction,
                            "severity": m.severity, "matched_keywords": m.matched_keywords,
                            "already_priced": m.already_priced,
                        }
                        for m in result.matches
                    ],
                    surprises={}, amplifiers=result.amplifiers,
                    exposed=exposed if result.is_notable else [],
                    occurred_at=occurred,
                    # Null, never zero. A replay has no latency, and a zero here
                    # would claim an instant reaction that never happened.
                    latency_seconds=None, scoring_ms=result.elapsed_ms,
                )
                if created is not None:
                    written += 1
                    if result.is_notable:
                        notable += 1
            db.commit()

        logger.info("Prior replay: %d new assessments, %d notable.", written, notable)
    except Exception:
        logger.exception("Prior replay failed.")
    finally:
        db.close()


def run_brief_refresh() -> None:
    """Recompute every stored brief from the findings currently in the database.

    A brief is a snapshot, which is right for an output that must not depend on
    a provider being up at read time, and wrong if nothing ever refreshes it.
    Free: no model call anywhere on this path.
    """
    from app.engine.pipeline import regenerate_brief
    from app.repositories.company_repository import CompanyRepository

    db = SessionLocal()
    try:
        rebuilt = 0
        for company in CompanyRepository(db).list_all():
            try:
                if regenerate_brief(company.ticker, db) is not None:
                    rebuilt += 1
            except Exception:
                logger.exception("Brief refresh failed for %s", company.ticker)
        db.commit()
        logger.info("Brief refresh: %d briefs rebuilt.", rebuilt)
    except Exception:
        logger.exception("Brief refresh failed.")
    finally:
        db.close()


def run_digests() -> None:
    """Send everybody who is due a digest of what moved in their companies.

    Free, like the other scheduled jobs: it reads what the engine already
    computed and sends plain text. The one thing it must not do is send when
    nothing happened, which the digest service enforces rather than this job.
    """
    from app.services.digest import send_due_digests

    db = SessionLocal()
    try:
        sent = send_due_digests(db)
        if sent:
            logger.info("Digests: %d sent.", sent)
    except Exception:
        logger.exception("Digest run failed.")
    finally:
        db.close()


def run_coverage_drip() -> None:
    """Close the coverage gap a few companies at a time.

    The one scheduled job that spends model quota, and the reason it is safe to
    put on a clock is that it is bounded twice: a small per-run limit, and a
    clean stop the moment the provider refuses. A refusal is not an error here,
    it is the expected steady state of a free tier, and the next run continues
    where this one left off.

    Priors before reads, because a prior covers every company and costs less,
    so it buys more coverage per call than reading a filing does.
    """
    from app.engine.coverage import drip_priors, drip_reads

    db = SessionLocal()
    try:
        priors = drip_priors(db)
        db.commit()
        if priors.covered or priors.remaining:
            logger.info(
                "Coverage: %d prior(s) built, %d still uncovered%s.",
                priors.covered, priors.remaining,
                ", quota exhausted" if priors.exhausted else "",
            )

        if priors.exhausted:
            # Nothing left for the read drip either, and asking would produce
            # the same refusal once more.
            return

        reads = drip_reads(db)
        db.commit()
        if reads.covered or reads.remaining:
            logger.info(
                "Coverage: %d compan%s read for the first time, %d never read%s.",
                reads.covered, "y" if reads.covered == 1 else "ies",
                reads.remaining, ", quota exhausted" if reads.exhausted else "",
            )
    except Exception:
        logger.exception("Coverage drip failed.")
    finally:
        db.close()
