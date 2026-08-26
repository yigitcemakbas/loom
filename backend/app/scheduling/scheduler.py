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
from app.scheduling.jobs import run_scheduled_refresh

logger = logging.getLogger(__name__)

_REFRESH_JOB_ID = "refresh-watchlist"

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
    scheduler.start()
    _scheduler = scheduler
    logger.info(
        "Scheduler started: refreshing the watchlist every %d minutes.",
        settings.scheduler_interval_minutes,
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


def _first_run_time():
    from datetime import datetime, timedelta, timezone

    return datetime.now(timezone.utc) + timedelta(
        seconds=settings.scheduler_startup_delay_seconds
    )
