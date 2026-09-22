"""APScheduler wiring: the app keeps itself current without anyone running a CLI.

Until Phase 3 every ingest was triggered by hand or by adding a ticker. That
made the dashboard a snapshot of whenever someone last remembered to refresh
it, which is the wrong shape for a tool whose whole claim is being one current
source of truth.

Job configuration matters more than the interval here:

  `max_instances=1`  A refresh can outlast its interval on a large watchlist.
                     Without this, APScheduler would start a second pass over
                     the same tickers while the first is still going, doubling
                     both network load and LLM spend.
  `coalesce=True`    If the machine sleeps through several fire times, run once
                     on wake, not once per missed slot.
  a startup delay    Nothing competes with app boot for the same database.

The scheduler runs in this process. That is the right call for a single-user
local tool and the wrong one for a multi-instance deployment, where two
replicas would each run every job, at which point this moves behind a real
task queue. Noted here rather than discovered later.
"""

import logging

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.interval import IntervalTrigger

from app.config import settings
from app.scheduling.jobs import (
    run_brief_refresh,
    run_coverage_drip,
    run_digests,
    run_factor_scoring,
    run_price_refresh,
    run_prior_replay,
    run_scheduled_refresh,
)

logger = logging.getLogger(__name__)

_REFRESH_JOB_ID = "refresh-watchlist"

# The background jobs, and why each runs when it does.
#
# All but one are arithmetic over stored data, or plain text over the result of
# it: no model call, no quota, no cost. The exception is the coverage drip,
# which is on this list because it is bounded twice (a small per-run limit and
# a clean stop when the provider refuses) rather than because it is free.
# That is what makes it reasonable to run them on a clock rather than asking a
# person to remember, and it is the difference between a workbench and an
# instrument. The expensive job above this line is the one that calls a model;
# everything below is free.
#
# Staggered rather than fired together, because they share a database and
# because the order matters where cadences coincide: prices before factors,
# since valuation is a ratio to a price, and factors before briefs, since a
# brief reads the scores.
_FREE_JOBS: tuple[tuple[str, object, str, int, int], ...] = (
    # id, callable, description, interval minutes, startup offset minutes
    (
        "refresh-prices", run_price_refresh,
        "Store yesterday's closes for the universe",
        # Daily. A session's close is final once it happens, so more often
        # would re-fetch the same bar.
        60 * 24, 3,
    ),
    (
        "replay-priors", run_prior_replay,
        "Score filings that arrived since the last pass against standing priors",
        # Every six hours, matching the document ingest above it: a filing
        # cannot be assessed before it has been stored.
        60 * 6, 8,
    ),
    (
        "score-factors", run_factor_scoring,
        "Rescore the universe on its filed financials",
        # Weekly. Fundamentals move only when somebody files, and a daily
        # rescore would burn cycles redrawing the same picture.
        60 * 24 * 7, 13,
    ),
    (
        "coverage-drip", run_coverage_drip,
        "Arm and read a few more companies, within whatever quota allows",
        # Every two hours. Frequent enough that the remaining hundred and
        # seventeen companies are covered in days rather than months, spaced
        # enough that a free tier which refuses bursts gets time to recover
        # between attempts.
        120, 5,
    ),
    (
        "send-digests", run_digests,
        "Email everybody who is due a digest of what moved in their companies",
        # Checked hourly, which is not how often anybody is sent one: each
        # account has its own frequency and its own window, so a frequent check
        # means a daily digest lands near the same hour rather than drifting by
        # however long the last run took. Nothing is sent on a quiet day.
        60, 29,
    ),
    (
        "refresh-briefs", run_brief_refresh,
        "Recompute every stored brief from current findings",
        # Daily, after prices. A brief is a snapshot, which is the right shape
        # for an output that must not depend on a provider being up at read
        # time and the wrong one if nothing ever refreshes it.
        60 * 24, 21,
    ),
)

_scheduler: BackgroundScheduler | None = None


def start_scheduler() -> BackgroundScheduler | None:
    """Start background jobs. Returns None when disabled by config."""
    global _scheduler

    if not settings.scheduler_enabled:
        logger.info("Scheduler disabled (SCHEDULER_ENABLED=false); no background jobs started.")
        return None
    if _scheduler is not None:
        return _scheduler

    scheduler = BackgroundScheduler(timezone="UTC")
    scheduler.add_job(
        run_scheduled_refresh,
        trigger=IntervalTrigger(
            minutes=settings.scheduler_interval_minutes,
            # Delay the first fire rather than running at import time.
            start_date=None,
        ),
        id=_REFRESH_JOB_ID,
        name="Re-ingest and re-analyse every watchlist ticker",
        max_instances=1,
        coalesce=True,
        misfire_grace_time=600,
        next_run_time=_first_run_time(),
    )
    for job_id, job, description, minutes, offset in _FREE_JOBS:
        scheduler.add_job(
            job,
            trigger=IntervalTrigger(minutes=minutes),
            id=job_id,
            name=description,
            max_instances=1,
            coalesce=True,
            # Generous, because these are the jobs most likely to be missed by
            # a laptop that slept: running one late is strictly better than
            # skipping it, since each is idempotent.
            misfire_grace_time=3600,
            next_run_time=_first_run_time(offset),
        )

    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "Scheduler started: watchlist every %d minutes, plus %d free jobs "
        "(prices daily, priors 6-hourly, factors weekly, briefs daily, "
        "digests hourly).",
        settings.scheduler_interval_minutes, len(_FREE_JOBS),
    )
    return scheduler


def shutdown_scheduler() -> None:
    global _scheduler
    if _scheduler is None:
        return
    # wait=False: a refresh can take minutes, and blocking shutdown on it would
    # make Ctrl-C look like a hang. The job is safe to interrupt, each ticker
    # commits independently.
    _scheduler.shutdown(wait=False)
    _scheduler = None
    logger.info("Scheduler stopped.")


def _first_run_time(offset_minutes: int = 0):
    """When a job should fire for the first time after boot.

    The offset staggers the quantitative jobs so they do not all wake at once
    on a shared database, and so none of them competes with app startup.
    """
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone.utc) + timedelta(
        seconds=settings.scheduler_startup_delay_seconds,
        minutes=offset_minutes,
    )
