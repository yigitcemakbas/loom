"""Centralized configuration.

This is the *only* module in the codebase allowed to read environment
variables (via pydantic-settings). Every other module receives config
through function/constructor arguments or by importing `settings` from
here, never via `os.environ` directly. This is one of the architecture
principles in docs/plan.md: config reads live in exactly one place.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Where Docker mounts secret files. Resolved to None when absent so running
# outside a container does not warn about a directory only expected to exist
# inside one.
_SECRETS_DIR = "/run/secrets" if Path("/run/secrets").is_dir() else None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        # Secrets are read from files, one per setting, named after the field
        # (/run/secrets/gemini_api_key). This is the difference between a key
        # that is only ever a file the process reads, and one that sits in the
        # container's environment where `docker inspect` and /proc/1/environ
        # both hand it to anyone who can talk to the Docker socket.
        #
        # Nothing breaks when the directory is absent or empty, which is what
        # keeps a no-key install working. An environment variable still wins
        # if set, so local development and CI can stay simple.
        secrets_dir=_SECRETS_DIR,
    )

    database_url: str = "postgresql+psycopg://loom:loom@localhost:5432/loom"

    # SEC EDGAR enforces its fair-access policy through this header, and the
    # enforcement is stricter than the published wording suggests. Measured
    # directly against sec.gov: a User-Agent must contain an email-shaped
    # token, and must NOT contain a URL. "Loom research-tool
    # (github.com/...)" is refused with 403; "Loom research-tool
    # name@domain" is accepted.
    #
    # The default therefore carries a reserved-documentation address so the
    # app works on a fresh clone with no configuration. It is a placeholder,
    # not a real mailbox, and users are asked in-app to replace it with their
    # own contact so SEC can reach a human about their traffic.
    sec_edgar_user_agent: str = "Loom research-tool loom@example.com"

    # Sent by every scraper (Phase 3+). Identifies this tool honestly and is
    # never a spoofed browser string: a site that wants to block Loom must be
    # able to, which is the whole premise of respecting robots.txt.
    scraper_user_agent: str = "Loom research-tool loom@example.com"

    finnhub_api_key: str = ""

    # Phase 3: periodic re-ingest + re-analysis of every watchlist ticker, so
    # the dashboard stays current without anyone running a CLI command.
    # Disabled during tests and any run that only wants the API surface.
    scheduler_enabled: bool = True
    scheduler_interval_minutes: int = 360
    # Delay before the first automatic run, so app startup is never competing
    # with a full ingest for the same network and database.
    scheduler_startup_delay_seconds: int = 120

    # Which LLM backs the analysis engine: "gemini" (free tier) or
    # "anthropic" (paid). Only the selected provider's key is needed.
    llm_provider: str = "gemini"
    anthropic_api_key: str = ""
    gemini_api_key: str = ""

    # Minimum gap between LLM requests, to stay under a per-minute quota rather
    # than repeatedly hitting it and paying the backoff. Gemini's free tier
    # allows roughly ten requests a minute, so ~6.5s spaces a batch just under
    # the cap. Raise it if runs still hit rate limits, set 0 to disable pacing
    # entirely (appropriate on a paid tier, where throughput matters more).
    llm_min_call_interval_seconds: float = 6.5

    blob_store_dir: Path = Path("./data/blobs")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
