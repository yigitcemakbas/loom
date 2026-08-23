"""Centralized configuration.

This is the *only* module in the codebase allowed to read environment
variables (via pydantic-settings). Every other module receives config
through function/constructor arguments or by importing `settings` from
here — never via `os.environ` directly. This is one of the architecture
principles in docs/plan.md: config reads live in exactly one place.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "postgresql+psycopg://loom:loom@localhost:5432/loom"

    # SEC EDGAR requires an honest, identifying User-Agent per their fair-access
    # policy — no API key, but this header is not optional.
    sec_edgar_user_agent: str = "Loom research-tool (set SEC_EDGAR_USER_AGENT)"

    finnhub_api_key: str = ""
    anthropic_api_key: str = ""

    blob_store_dir: Path = Path("./data/blobs")


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
