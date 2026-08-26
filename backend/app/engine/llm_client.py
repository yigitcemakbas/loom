"""The single point of contact with any LLM provider.

Everything else in the engine builds prompts and interprets results; only this
module makes network calls. That boundary is what let the project switch from
Anthropic to Gemini without touching extraction, diffing, the pipeline, or any
prompt, the concrete client changes, the interface does not.

Two providers are supported:

  gemini     Google's free tier. 1M context, no cost, subject to rate limits.
             The default, because Loom's filings are large and free-tier
             quota covers them.
  anthropic  Claude via the paid API. Higher quality, billed per token.

Selected with LLM_PROVIDER in backend/.env.
"""

import logging
import random
import threading
import time
from abc import ABC, abstractmethod
from typing import TypeVar

import httpx
from pydantic import BaseModel

from app.config import settings

logger = logging.getLogger(__name__)

# Bumping this invalidates prior analyses and is the deliberate way to
# reprocess documents after changing a prompt (see DocumentAnalysis).
PROMPT_VERSION = "2026-08-26.1"

T = TypeVar("T", bound=BaseModel)


class LLMUnavailableError(RuntimeError):
    """Analysis cannot run for a configuration reason, no API key, an
    unbillable account, or exhausted quota. Callers skip cleanly instead of
    surfacing a raw provider error from deep inside a batch job.

    Distinguished from genuine failures because the fix is an account action
    by the user, not a retry or a code change.
    """


class LLMClient(ABC):
    """What the rest of the engine depends on. Providers implement this."""

    model: str
    # Dollars per million tokens; zero for free tiers.
    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.calls = 0

    @property
    @abstractmethod
    def available(self) -> bool:
        """True when this client is configured well enough to try a call."""

    @abstractmethod
    def parse(self, *, system: str, user_content: str, schema: type[T], max_tokens: int = 16000) -> T | None:
        """Run one structured-output call and return a validated model.

        Returns None when the response cannot be validated after a retry, a
        single unparseable document should not abort a batch. Configuration
        problems raise LLMUnavailableError instead, because they affect
        everything and need the user's attention.
        """

    @property
    def cost_usd(self) -> float:
        return (
            self.input_tokens / 1_000_000 * self.input_cost_per_mtok
            + self.output_tokens / 1_000_000 * self.output_cost_per_mtok
        )

    def usage_summary(self) -> str:
        cost = "free tier" if self.cost_usd == 0 else f"${self.cost_usd:.2f}"
        return (
            f"{self.calls} calls, {self.input_tokens:,} in / "
            f"{self.output_tokens:,} out, {cost}"
        )


class GeminiClient(LLMClient):
    """Google Gemini via the free tier of the Google AI Studio API."""

    # Flash is the right tier here: the work is extraction against a large
    # input, not open-ended reasoning, and Flash has the widest free quota.
    #
    # gemini-3.7-flash (the newest) was tried first originally, but measured
    # live: its free-tier quota was saturated hard enough that every single
    # call needed two ~20-40s backoff waits before falling back, roughly a
    # minute of pure waste per document. gemini-3.6-flash answered every one
    # of those fallback attempts immediately, so it is the primary instead.
    # 3.7 stays as a fallback in case 3.6's quota is what's tight another day.
    model = "gemini-3.6-flash"

    # Free-tier capacity is shared and shifts over time, so a request can fail
    # repeatedly on one model while another serves fine. When the primary is
    # saturated, drop to the next rather than fail the document. All are
    # pinned versions: the "-latest" aliases measured less reliable, and an
    # alias could also change behaviour underneath us without warning.
    _FALLBACK_MODELS = ("gemini-3.5-flash", "gemini-3.7-flash")

    # 503 under load is transient and worth waiting out rather than failing a
    # document over.
    _TRANSIENT_ATTEMPTS = 3
    _BACKOFF_BASE_SECONDS = 2.0
    # Per-minute caps need a longer wait than a busy-server retry.
    _RATE_LIMIT_BACKOFF_SECONDS = 20.0

    # Pacing, not backoff. Backoff is reactive: it only runs after a request
    # has already been rejected, so a batch that outruns the per-minute cap
    # spends most of its time in penalty waits. Measured over a real backfill,
    # 14 of 16 transient failures were rate limits, each costing ~60s of
    # retries plus a model fallback before the document even got a verdict.
    # Spacing requests to stay under the cap avoids the rejection entirely, and
    # a call that succeeds first time is far cheaper than one that succeeds on
    # the third model.
    #
    # Class-level, deliberately: the pipeline builds a fresh client per ticker,
    # so per-instance pacing would reset on every ticker and the cap is
    # per-key, not per-client.
    _last_call_at: float = 0.0
    _pacing_lock = threading.Lock()

    def __init__(self, api_key: str | None = None):
        super().__init__()
        self._api_key = api_key if api_key is not None else settings.gemini_api_key
        self._client = None

    @classmethod
    def _pace(cls) -> None:
        """Block until the configured minimum gap since the last call has passed."""
        interval = settings.llm_min_call_interval_seconds
        if interval <= 0:
            return
        with cls._pacing_lock:
            wait = interval - (time.monotonic() - cls._last_call_at)
            if wait > 0:
                time.sleep(wait)
            cls._last_call_at = time.monotonic()

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _ensure_client(self):
        if not self._api_key:
            raise LLMUnavailableError(
                "GEMINI_API_KEY is not set. Create a free key at "
                "aistudio.google.com/apikey and add it to backend/.env."
            )
        if self._client is None:
            from google import genai
            from google.genai import types

            # Two things measured directly against the real API, both fixed
            # here:
            #
            # 1. With no explicit timeout, a stalled connection hung with no
            #    response and no error for 15+ minutes.
            # 2. The SDK retries failures internally (5 attempts by default,
            #    backoff up to 60s each) *underneath* this client's own
            #    retry/fallback loop. The two multiplied together, 3 of our
            #    attempts x 3 fallback models x 5 SDK-internal attempts, and
            #    one call took nearly two hours to finally surface an error.
            #    Disabling the SDK's internal retry makes this client's loop
            #    the only one, so total time stays bounded and predictable.
            self._client = genai.Client(
                api_key=self._api_key,
                http_options=types.HttpOptions(
                    timeout=90_000,  # milliseconds
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            )
        return self._client

    def _generate(self, *, system: str, user_content: str, schema: type[T], max_tokens: int):
        """One request: retry transient errors, then fall back to another model."""
        last_error: Exception | None = None
        for model in (self.model, *self._FALLBACK_MODELS):
            try:
                return self._generate_on(
                    model, system=system, user_content=user_content,
                    schema=schema, max_tokens=max_tokens,
                )
            except LLMUnavailableError as exc:
                last_error = exc
                if model != self._FALLBACK_MODELS[-1]:
                    logger.warning("Falling back from %s to the next model.", model)
        raise last_error  # type: ignore[misc]

    def _generate_on(self, model: str, *, system: str, user_content: str, schema: type[T], max_tokens: int):
        """One request against one model, retrying transient errors with backoff."""
        from google.genai import errors as genai_errors

        client = self._ensure_client()
        last_error: Exception | None = None

        for attempt in range(self._TRANSIENT_ATTEMPTS):
            try:
                self._pace()
                return client.models.generate_content(
                    model=model,
                    contents=user_content,
                    config={
                        "system_instruction": system,
                        "response_mime_type": "application/json",
                        "response_schema": schema,
                        "max_output_tokens": max_tokens,
                    },
                )
            except genai_errors.ClientError as exc:
                # A free-tier 429 is usually the per-minute cap, which clears
                # on its own, a batch should wait rather than abort. A daily
                # quota exhaustion looks the same, so after exhausting retries
                # it is reported as a configuration problem.
                if "RESOURCE_EXHAUSTED" not in str(exc) and "429" not in str(exc):
                    raise
                last_error = exc
                if attempt == self._TRANSIENT_ATTEMPTS - 1:
                    break
                delay = self._RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1) + random.uniform(0, 2)
                logger.warning(
                    "Rate limited (attempt %d/%d); waiting %.0fs.",
                    attempt + 1, self._TRANSIENT_ATTEMPTS, delay,
                )
                time.sleep(delay)
            except genai_errors.ServerError as exc:
                last_error = exc
                if attempt == self._TRANSIENT_ATTEMPTS - 1:
                    break
                # Jitter so a batch of documents doesn't retry in lockstep.
                delay = self._BACKOFF_BASE_SECONDS * (2**attempt) + random.uniform(0, 1)
                logger.warning(
                    "%s is busy (attempt %d/%d); retrying in %.1fs.",
                    model, attempt + 1, self._TRANSIENT_ATTEMPTS, delay,
                )
                time.sleep(delay)
            except httpx.TransportError as exc:
                # A read timeout or dropped connection is the most transient
                # failure there is, yet without this clause it was the *least*
                # tolerated: 503s got three attempts across three models while
                # a timeout aborted the call outright. Observed live on the
                # year-over-year diff, whose prompt is the largest the engine
                # sends and so the likeliest to exceed the request timeout.
                last_error = exc
                if attempt == self._TRANSIENT_ATTEMPTS - 1:
                    break
                delay = self._BACKOFF_BASE_SECONDS * (2**attempt) + random.uniform(0, 1)
                logger.warning(
                    "%s network error (%s) on attempt %d/%d; retrying in %.1fs.",
                    model, type(exc).__name__, attempt + 1, self._TRANSIENT_ATTEMPTS, delay,
                )
                time.sleep(delay)

        # The three transient failures need different messages, because they
        # need different responses from whoever reads the log: wait, retry, or
        # check the network.
        if "RESOURCE_EXHAUSTED" in str(last_error) or "429" in str(last_error):
            detail = "the free-tier quota is exhausted (it resets on a rolling window)"
        elif isinstance(last_error, httpx.TransportError):
            detail = (
                f"the request kept failing at the network layer "
                f"({type(last_error).__name__}); large prompts are the usual cause"
            )
        else:
            detail = "the free tier is under heavy load"
        raise LLMUnavailableError(
            f"{model} is unavailable after {self._TRANSIENT_ATTEMPTS} attempts, "
            f"{detail}. Try again shortly."
        ) from last_error

    def parse(self, *, system: str, user_content: str, schema: type[T], max_tokens: int = 16000) -> T | None:
        from google.genai import errors as genai_errors

        for attempt in (1, 2):
            try:
                response = self._generate(
                    system=system, user_content=user_content, schema=schema, max_tokens=max_tokens
                )
            except genai_errors.ClientError as exc:
                message = str(exc)
                # 429 on the free tier means quota, not a transient blip: it
                # affects every subsequent call, so stop rather than grind
                # through a batch failing one document at a time.
                if "RESOURCE_EXHAUSTED" in message or "429" in message:
                    raise LLMUnavailableError(
                        "Gemini free-tier quota is exhausted. It resets on a rolling "
                        "window, retry later, or reduce how many filings are analysed."
                    ) from exc
                if "API_KEY_INVALID" in message or "API key not valid" in message:
                    raise LLMUnavailableError(
                        "The Gemini API key was rejected. Check GEMINI_API_KEY in backend/.env."
                    ) from exc
                logger.error("Gemini client error: %s", message[:300])
                raise

            self.calls += 1
            usage = response.usage_metadata
            if usage is not None:
                self.input_tokens += usage.prompt_token_count or 0
                self.output_tokens += usage.candidates_token_count or 0

            if response.parsed is not None:
                return response.parsed

            if attempt == 1:
                logger.warning("Structured output failed validation; retrying once.")

        logger.error("Structured output could not be validated after a retry; skipping.")
        return None


class AnthropicClient(LLMClient):
    """Claude via the paid Anthropic API."""

    model = "claude-opus-5"
    input_cost_per_mtok = 5.00
    output_cost_per_mtok = 25.00

    def __init__(self, api_key: str | None = None):
        super().__init__()
        self._api_key = api_key if api_key is not None else settings.anthropic_api_key
        self._client = None

    @property
    def available(self) -> bool:
        return bool(self._api_key)

    def _ensure_client(self):
        if not self._api_key:
            raise LLMUnavailableError(
                "ANTHROPIC_API_KEY is not set; add it to backend/.env to enable analysis."
            )
        if self._client is None:
            import anthropic

            self._client = anthropic.Anthropic(api_key=self._api_key)
        return self._client

    def parse(self, *, system: str, user_content: str, schema: type[T], max_tokens: int = 16000) -> T | None:
        import anthropic

        client = self._ensure_client()

        for attempt in (1, 2):
            try:
                response = client.messages.parse(
                    model=self.model,
                    max_tokens=max_tokens,
                    system=system,
                    output_config={"effort": "high"},
                    output_format=schema,
                    messages=[{"role": "user", "content": user_content}],
                )
            except anthropic.AuthenticationError as exc:
                raise LLMUnavailableError(
                    "The Anthropic API rejected the configured key. Check "
                    "ANTHROPIC_API_KEY in backend/.env."
                ) from exc
            except anthropic.RateLimitError:
                logger.warning("Rate limited by the Anthropic API; not retrying inline.")
                raise
            except anthropic.APIStatusError as exc:
                # A credit-balance failure arrives as a 400, not a dedicated
                # error type, so it has to be recognised by message. Without
                # this it reads as a malformed-request bug, which sends the
                # reader looking in entirely the wrong place.
                if "credit balance" in str(exc).lower():
                    raise LLMUnavailableError(
                        "The Anthropic account has insufficient credit. Add credit at "
                        "console.anthropic.com under Plans & Billing, then retry."
                    ) from exc
                logger.error("Anthropic API error %s: %s", exc.status_code, exc.message)
                raise
            except anthropic.APIConnectionError:
                logger.exception("Could not reach the Anthropic API.")
                raise

            self.calls += 1
            if response.usage is not None:
                self.input_tokens += response.usage.input_tokens or 0
                self.output_tokens += response.usage.output_tokens or 0

            if response.parsed_output is not None:
                return response.parsed_output

            if attempt == 1:
                logger.warning("Structured output failed validation; retrying once.")

        logger.error("Structured output could not be validated after a retry; skipping.")
        return None


_PROVIDERS: dict[str, type[LLMClient]] = {
    "gemini": GeminiClient,
    "anthropic": AnthropicClient,
}


def get_llm_client() -> LLMClient:
    """Single construction point. Adding a provider touches only this file."""
    provider = (settings.llm_provider or "gemini").lower()
    client_class = _PROVIDERS.get(provider)
    if client_class is None:
        raise LLMUnavailableError(
            f"Unknown LLM_PROVIDER {provider!r}. Valid options: {', '.join(_PROVIDERS)}."
        )
    return client_class()
