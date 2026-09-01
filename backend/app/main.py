from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

import app.models  # noqa: F401  (registers all models before any relationship resolution)
from app.api.routes import (
    briefs,
    companies,
    earnings,
    prices,
    dashboard,
    documents,
    facts,
    signals,
    status,
    tape,
    watchlists,
)
from app.scheduling.scheduler import shutdown_scheduler, start_scheduler


@asynccontextmanager
async def lifespan(_: FastAPI):
    """Background refresh lives for exactly as long as the app does.

    Started here rather than at import time so that importing `app.main`
    (tests, Alembic, a CLI script) never silently spawns a scheduler thread
    that then competes for the database.
    """
    start_scheduler()
    yield
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
