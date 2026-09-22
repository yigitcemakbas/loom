import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.models  # noqa: F401  (registers all models before any relationship resolution)
from app.config import settings
from app.api.routes import (
    admin,
    assessments,
    auth,
    briefs,
    case,
    changes,
    companies,
    contradictions,
    earnings,
    exposure,
    factors,
    positions,
    prices,
    priors,
    dashboard,
    documents,
    facts,
    signals,
    status,
    tape,
    watchlists,
)
from app.scheduling.scheduler import shutdown_scheduler, start_scheduler
from app.scheduling.watcher import start_watcher, stop_watcher


def _configure_logging() -> None:
    """Make the background work visible.

    Uvicorn configures only its own loggers, leaving the root logger at
    WARNING, which silently discards every INFO line this application emits.
    The symptom is badly misleading: the scheduler and filing watcher run
    perfectly and report nothing, so a working system looks exactly like one
    that never started.
    """
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        force=True,
    )
    # These are chatty at INFO and drown out everything worth reading.
    for noisy in ("httpx", "httpcore", "google_genai", "urllib3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def _reconcile_admins() -> None:
    """Make the database agree with ADMIN_USERNAMES on every boot.

    The policy is also applied at registration and on each sign-in, which
    covers the normal cases. This covers the two that are easy to miss: an
    account that existed before a username was added to the list, and an
    account whose row was edited directly. Without it, promoting somebody
    requires them to sign in again before the change takes effect, which looks
    like the setting not working.

    Idempotent and cheap: one statement over a table with as many rows as there
    are people using the instance. Guarded because a database that is not ready
    must not stop the API coming up.
    """
    from sqlalchemy import select

    from app.db.session import SessionLocal
    from app.models.account import User
    from app.services.auth import apply_admin_policy

    try:
        with SessionLocal() as db:
            changed = 0
            for user in db.execute(select(User)).scalars():
                before = user.is_admin
                apply_admin_policy(user)
                changed += int(before != user.is_admin)
            if changed:
                db.commit()
                logging.getLogger(__name__).info(
                    "Admin policy applied: %d account(s) changed.", changed
                )
    except Exception:
        logging.getLogger(__name__).warning(
            "Could not reconcile admin accounts at startup.", exc_info=True
        )


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Background refresh lives for exactly as long as the app does.

    Started here rather than at import time so that importing `app.main`
    (tests, Alembic, a CLI script) never silently spawns a scheduler thread
    that then competes for the database.
    """
    _configure_logging()
    _reconcile_admins()
    start_scheduler()
    start_watcher()
    yield
    stop_watcher()
    shutdown_scheduler()


app = FastAPI(title="Loom API", version="0.1.0", lifespan=lifespan)

# Single-user MVP bound to localhost. The frontend normally reaches the API
# through Vite's same-origin proxy (see frontend/vite.config.ts), so CORS is
# not involved at all. This regex is a backstop for pointing a browser
# directly at the API: it accepts any local port, because pinning one exact
# port silently broke the app whenever Vite bound elsewhere (5174, 5175, ...)
# after finding its default port taken.
app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1|\[::1\]):\d+",
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(positions.router)
app.include_router(companies.router)
app.include_router(watchlists.router)
app.include_router(documents.router)
app.include_router(signals.router)
app.include_router(dashboard.router)
app.include_router(status.router)
app.include_router(facts.router)
app.include_router(briefs.router)
app.include_router(earnings.router)
app.include_router(prices.router)
app.include_router(tape.router)
app.include_router(assessments.router)
app.include_router(exposure.router)
app.include_router(factors.router)
app.include_router(contradictions.router)
app.include_router(changes.router)
app.include_router(case.router)
app.include_router(priors.router)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/capabilities")
def capabilities():
    """Which sources are active, and what is missing without a key.

    Exists so the app can say plainly what it can and cannot do rather than
    letting a user discover it by clicking something that quietly fails. No
    key is required to run Loom: filings, transcripts, insider records and
    prices all work unconfigured. This endpoint is what makes that legible.
    """
    from app.config import settings

    has_gemini = bool(settings.gemini_api_key)
    has_anthropic = bool(settings.anthropic_api_key)
    has_llm = has_gemini if settings.llm_provider == "gemini" else has_anthropic
    has_finnhub = bool(settings.finnhub_api_key)

    return {
        "sources": {
            "sec_filings": {"active": True, "needs_key": False},
            "insider_transactions": {"active": True, "needs_key": False},
            "earnings_transcripts": {"active": True, "needs_key": False},
            "prices": {"active": True, "needs_key": False},
            "company_news": {
                "active": has_finnhub,
                "needs_key": True,
                "key_name": "FINNHUB_API_KEY",
                "get_key_at": "https://finnhub.io/register",
            },
            "earnings_calendar": {
                "active": has_finnhub,
                "needs_key": True,
                "key_name": "FINNHUB_API_KEY",
                "get_key_at": "https://finnhub.io/register",
            },
        },
        "analysis": {
            "active": has_llm,
            "provider": settings.llm_provider,
            "needs_key": True,
            "key_name": "GEMINI_API_KEY" if settings.llm_provider == "gemini" else "ANTHROPIC_API_KEY",
            "get_key_at": (
                "https://aistudio.google.com/apikey"
                if settings.llm_provider == "gemini"
                else "https://console.anthropic.com"
            ),
        },
        # Everything below works with no configuration at all.
        "always_available": ["document search", "insider tracking", "price charts", "filing comparison"],
    }
