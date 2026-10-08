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

import json
import logging
import random
import re
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

    # Short identifier, used wherever a log line or a usage summary has to say
    # which provider served a call. On the interface rather than per subclass
    # because ChainClient reports across providers and cannot guess.
    name: str = "llm"
    # Set by ChainClient on every provider but the last. Waiting out a rate
    # limit is worth a minute when there is no alternative and is pure latency
    # when the next provider is idle, so patience is a property of position in
    # the chain rather than of the provider.
    fail_fast: bool = False
    model: str
    # Dollars per million tokens; zero for free tiers.
    input_cost_per_mtok: float = 0.0
    output_cost_per_mtok: float = 0.0

    # Quota state lives here rather than on one provider family, because every
    # provider has an allowance and the chain has to treat them alike. On the
    # class, not the instance: quota belongs to the key and the pipeline builds
    # a fresh client per ticker, so an instance attribute would shadow this and
    # rediscover a spent allowance once per company.
    exhausted: bool = False
    #: When it was marked. Zero means "set by hand" and stays until cleared by
    #: hand, which is what the tests rely on.
    exhausted_at: float = 0.0
    #: Free allowances reset on rolling windows, so holding the flag for the
    #: life of the process turns one bad hour into a dead day. The scheduler
    #: drips every two hours; this clears well inside that.
    quota_cooldown_seconds: float = 30 * 60

    def _mark_spent(self) -> None:
        """Record a spent allowance, with the time it happened."""
        cls = type(self)
        cls.exhausted = True
        cls.exhausted_at = time.monotonic()

    def _allowance_available(self) -> bool:
        """False while a recorded exhaustion is still inside its window."""
        cls = type(self)
        if (
            cls.exhausted
            and cls.exhausted_at
            and time.monotonic() - cls.exhausted_at >= self.quota_cooldown_seconds
        ):
            logger.info("%s: cooldown elapsed; trying it again.", self.name)
            cls.exhausted = False
            cls.exhausted_at = 0.0
        return not cls.exhausted

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
            f"{self.name}: {self.calls} calls, {self.input_tokens:,} in / "
            f"{self.output_tokens:,} out, {cost}"
        )


class GeminiClient(LLMClient):
    """Google Gemini via the free tier of the Google AI Studio API."""

    name = "gemini"

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
        return bool(self._api_key) and self._allowance_available()

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

        attempts = 1 if self.fail_fast else self._TRANSIENT_ATTEMPTS
        for attempt in range(attempts):
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
                if attempt == attempts - 1:
                    break
                delay = self._RATE_LIMIT_BACKOFF_SECONDS * (attempt + 1) + random.uniform(0, 2)
                logger.warning(
                    "Rate limited (attempt %d/%d); waiting %.0fs.",
                    attempt + 1, self._TRANSIENT_ATTEMPTS, delay,
                )
                time.sleep(delay)
            except genai_errors.ServerError as exc:
                last_error = exc
                if attempt == attempts - 1:
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
            # The comment above this method says a spent allowance affects every
            # later call, so the batch should stop rather than fail one document
            # at a time. The flag that does that was never set, so the chain
            # re-probed a dead provider for every single document: three model
            # fallbacks with backoff, about twenty seconds, per read.
            self._mark_spent()
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

        for attempt in ((1,) if self.fail_fast else (1, 2)):
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
                    self._mark_spent()
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
    name = "anthropic"

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
        return bool(self._api_key) and self._allowance_available()

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


class OpenAICompatibleClient(LLMClient):
    """Any provider speaking the OpenAI chat-completions dialect.

    One implementation covers Cerebras, Groq, Mistral and OpenRouter, because
    the dialect is the same and only the base URL, model name and limits differ.
    Written against httpx rather than the `openai` SDK: the call is one POST
    with a JSON body, httpx is already a dependency, and pulling in an SDK to
    reach four providers that deliberately speak a common protocol would add a
    package without adding a capability.

    **Structured output is attempted twice, in descending strictness.** Free
    catalogues are inconsistent here: `json_schema` with `strict` is honoured by
    the GPT-OSS and Nemotron families and rejected outright by others, so a
    refusal falls back to `json_object` with the schema inlined in the prompt.
    Either way the result goes through Pydantic, so a provider that ignores the
    instruction fails validation rather than returning something unchecked.

    **`max_input_tokens` is the field that matters and the one a quota page
    buries.** Free tiers advertise requests per day and are actually gated on
    tokens per minute, and the ceilings differ by an order of magnitude: around
    6,000 for Groq's small models against roughly 60,000 for Cerebras. Loom's
    inputs differ by a similar factor, since a 10-K risk section runs to about
    20,000 tokens while a fifteen-item news batch is nearer 1,000. Declaring the
    ceiling lets the chain route around a provider that cannot physically take
    the request instead of discovering it as a 429.
    """

    base_url: str = ""
    model: str = ""
    fallback_models: tuple[str, ...] = ()
    # Conservative: a request at the ceiling leaves no room for the response.
    max_input_tokens: int = 30_000
    supports_json_schema: bool = True
    min_interval_seconds: float = 2.0
    _TRANSIENT_ATTEMPTS = 3
    _BACKOFF_BASE_SECONDS = 2.0

    # Per-class, like GeminiClient's: the limit belongs to the key, not to the
    # instance, and the pipeline builds a fresh client per ticker.
    _last_call_at: float = 0.0
    _pacing_lock = threading.Lock()

    # Set when the provider reports its quota gone, so a batch stops paying a
    # round trip per document to rediscover it.
    #
    # A class attribute, and never assigned on the instance. Quota belongs to
    # the API key, and the pipeline builds a fresh client per ticker, so an
    # instance attribute would shadow this and reset the flag on every ticker.
    # The first version of this did exactly that and a test caught it.
    def __init__(self, api_key: str | None = None):
        super().__init__()
        self._api_key = (api_key if api_key is not None else self._configured_key()) or ""

    @classmethod
    def _configured_key(cls) -> str:
        return ""

    @property
    def available(self) -> bool:
        return bool(self._api_key) and self._allowance_available()

    def accepts(self, user_content: str, system: str = "") -> bool:
        """Whether this provider's per-minute ceiling can take the request.

        Four characters per token is the usual rough conversion and is accurate
        enough for a routing decision; being wrong by 20% changes nothing, while
        sending a 20,000-token filing to a 6,000-token lane wastes a round trip
        and a retry cycle every time.
        """
        return estimate_tokens(system) + estimate_tokens(user_content) <= self.max_input_tokens

    def _pace(self) -> None:
        with type(self)._pacing_lock:
            gap = time.monotonic() - type(self)._last_call_at
            wait = self.min_interval_seconds - gap
            if wait > 0:
                time.sleep(wait)
            type(self)._last_call_at = time.monotonic()

    def _body(self, model: str, system: str, user_content: str, schema: type[T],
              max_tokens: int, strict: bool) -> dict:
        instructions = system
        if not strict:
            # The schema has to reach the model somehow when the provider will
            # not enforce it, otherwise `json_object` returns well-formed JSON
            # of entirely the wrong shape.
            instructions = (
                f"{system}\n\nReturn JSON matching this schema exactly. "
                f"Emit no prose, no markdown fence, and no keys outside it:\n"
                f"{json.dumps(schema.model_json_schema())}"
            )
        body = {
            "model": model,
            "messages": [
                {"role": "system", "content": instructions},
                {"role": "user", "content": user_content},
            ],
            "max_tokens": max_tokens,
            "temperature": 0,
        }
        if strict:
            body["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": schema.__name__,
                    "strict": True,
                    "schema": schema.model_json_schema(),
                },
            }
        else:
            body["response_format"] = {"type": "json_object"}
        return body

    def _post(self, body: dict) -> dict:
        self._pace()
        with httpx.Client(timeout=120.0) as client:
            response = client.post(
                f"{self.base_url}/chat/completions",
                headers={"Authorization": f"Bearer {self._api_key}",
                         "Content-Type": "application/json"},
                json=body,
            )
        if response.status_code == 429:
            # Two different failures arrive as 429 and they need opposite
            # responses. A per-minute ceiling clears by itself in under a
            # minute; a spent daily allowance does not. Treating both as
            # exhaustion is what stopped the engine: Cerebras answers a large
            # filing with "Tokens per minute limit exceeded" and `Retry-After:
            # 60`, and the provider was then marked dead on the class for the
            # rest of the process. Every drip after that reported no providers
            # available without making a single request.
            retry_after = response.headers.get("retry-after")
            body = response.text[:400].lower()
            per_minute = (
                retry_after is not None
                or "per minute" in body
                or "per-minute" in body
                or "rate limit" in body
                or "too_many_tokens" in body
            )
            if per_minute:
                try:
                    wait = float(retry_after) if retry_after else 0.0
                except ValueError:
                    wait = 0.0
                raise _Transient(
                    f"{self.name}: per-minute ceiling reached"
                    f"{f'; clears in {wait:.0f}s' if wait else ''}.",
                    retry_after=wait,
                )
            raise _QuotaExhausted(f"{self.name}: allowance spent.")
        if response.status_code in (401, 403):
            raise LLMUnavailableError(f"{self.name} rejected the API key.")
        if response.status_code >= 500:
            raise _Transient(f"{self.name} returned {response.status_code}.")
        if response.status_code == 400 and "response_format" in response.text:
            raise _SchemaUnsupported(response.text[:200])
        if response.status_code == 404:
            # These catalogues turn over without notice: Groq's entire Llama
            # line vanished between this client being written and first run,
            # and a 404 here means the model name, not the endpoint. Treated as
            # "try the next model" so one deprecation does not take a whole
            # provider down, which is what `raise_for_status` did.
            raise _ModelGone(
                f"{self.name} does not serve this model. Check "
                f"{self.base_url}/models for the current catalogue."
            )
        response.raise_for_status()
        return response.json()

    def parse(self, *, system: str, user_content: str, schema: type[T],
              max_tokens: int = 16000) -> T | None:
        if not self._api_key:
            raise LLMUnavailableError(f"No API key configured for {self.name}.")

        modes = (True, False) if self.supports_json_schema else (False,)
        last: Exception | None = None

        for model in (self.model, *self.fallback_models):
            gone = False
            for strict in modes:
                if gone:
                    break
                attempts = 1 if self.fail_fast else self._TRANSIENT_ATTEMPTS
                for attempt in range(attempts):
                    try:
                        payload = self._post(
                            self._body(model, system, user_content, schema, max_tokens, strict)
                        )
                    except _ModelGone as exc:
                        logger.warning("%s: %s", self.name, exc)
                        last = exc
                        gone = True
                        break
                    except _SchemaUnsupported as exc:
                        # Try the looser mode on this same model rather than
                        # abandoning a provider that can still do the job.
                        logger.info("%s rejected json_schema; using json_object.", self.name)
                        last = exc
                        break
                    except _Transient as exc:
                        last = exc
                        if attempt == attempts - 1:
                            break
                        # The server's own Retry-After when it sent one: an
                        # exponential ladder starting under a second cannot
                        # wait out a sixty second window.
                        stated = getattr(exc, "retry_after", 0.0)
                        delay = stated or self._BACKOFF_BASE_SECONDS * (2**attempt)
                        time.sleep(delay + random.uniform(0, 1))
                        continue
                    except _QuotaExhausted as exc:
                        # Marked on the class: the quota is the key's, and every
                        # later client built from it is equally out.
                        self._mark_spent()
                        raise LLMUnavailableError(str(exc)) from exc

                    self.calls += 1
                    usage = payload.get("usage") or {}
                    self.input_tokens += usage.get("prompt_tokens") or 0
                    self.output_tokens += usage.get("completion_tokens") or 0

                    text = ((payload.get("choices") or [{}])[0].get("message") or {}).get("content")
                    parsed = _validate(text, schema)
                    if parsed is not None:
                        return parsed
                    logger.warning("%s returned output that failed validation.", self.name)
                    last = LLMUnavailableError(f"{self.name}: unvalidatable output.")
                    break

        if isinstance(last, (_Transient, _ModelGone)):
            # The provider could not answer, which is different from answering
            # badly, and the chain logs the two differently. Reporting a
            # per-minute ceiling as "no usable output" is how a rate limit got
            # mistaken for a prompt problem for six days.
            raise LLMUnavailableError(str(last)) from last
        if last is not None:
            logger.error("%s could not produce valid output: %s", self.name, str(last)[:200])
        return None


class _Transient(RuntimeError):
    """Retryable. `retry_after` is the server's own figure when it gives one,
    which beats guessing with an exponential ladder."""

    def __init__(self, *args, retry_after: float = 0.0):
        super().__init__(*args)
        self.retry_after = retry_after

    """A server-side blip worth retrying."""


class _QuotaExhausted(RuntimeError):
    """The key's allowance is gone; another provider should take over."""


class _SchemaUnsupported(RuntimeError):
    """The provider refused strict schema enforcement."""


class _ModelGone(RuntimeError):
    """The model name is no longer served. Try the next one."""


def estimate_tokens(text: str) -> int:
    """Rough token count. Four characters per token, the usual approximation."""
    return len(text or "") // 4


def _validate(text: str | None, schema: type[T]) -> T | None:
    """Parse a model's text into the schema, tolerating a markdown fence.

    Models told to emit bare JSON wrap it in ```json anyway often enough that
    stripping the fence is worth more than being strict about it; the content
    still has to validate.
    """
    if not text:
        return None
    body = text.strip()
    if body.startswith("```"):
        body = re.sub(r"^```(?:json)?\s*|\s*```$", "", body, flags=re.S)
    try:
        return schema.model_validate_json(body)
    except Exception:
        # A model that prefixed a sentence still usually emitted one object.
        match = re.search(r"\{.*\}", body, re.S)
        if match is None:
            return None
        try:
            return schema.model_validate_json(match.group(0))
        except Exception:
            return None


class CerebrasClient(OpenAICompatibleClient):
    """Cerebras. The largest free daily allowance, and the widest lane.

    Roughly a million tokens a day at 60,000 per minute, which is what makes it
    the one free provider that can take a full 10-K risk section without being
    chopped up. Its catalogue is volatile — it has dropped from a dozen models
    to two without notice — so the fallback list matters more here than
    elsewhere.
    """

    name = "cerebras"
    base_url = "https://api.cerebras.ai/v1"
    # Verified against https://api.cerebras.ai/v1/models. The catalogue is
    # volatile — it has dropped from a dozen models to two without notice — so
    # these are both of what it currently serves rather than a preference.
    model = "gpt-oss-120b"
    fallback_models = ("qwen-3.8-27b",)
    # Measured against the live endpoint rather than read off a docs page: on a
    # clean window 26,400 tokens is accepted and 30,500 is refused, so the
    # per-minute budget is about 30k and it has to cover the reply too. 45,000
    # was a guess, and it meant every filing routed here was refused on arrival.
    max_input_tokens = 22_000
    min_interval_seconds = 2.0

    @classmethod
    def _configured_key(cls) -> str:
        return settings.cerebras_api_key


class GroqClient(OpenAICompatibleClient):
    """Groq. Many requests a day through a narrow per-minute lane.

    Thousands of requests daily but as little as 6,000 tokens a minute, so it is
    the wrong provider for a filing and the right one for Loom's small inputs:
    a fifteen-item news batch is about a thousand tokens, and news is the
    largest corpus Loom holds. `max_input_tokens` is set to keep filings out of
    this lane rather than to describe the model's context window.
    """

    name = "groq"
    base_url = "https://api.groq.com/openai/v1"
    # Verified against https://api.groq.com/openai/v1/models. The entire Llama
    # line this was first written against (llama-3.3-70b-versatile and
    # llama-3.1-8b-instant) had already been withdrawn by first run, which is
    # why a 404 falls through to the next model instead of failing the provider.
    model = "openai/gpt-oss-120b"
    fallback_models = ("openai/gpt-oss-20b", "qwen/qwen3.8-27b")
    max_input_tokens = 5_000
    min_interval_seconds = 2.0

    @classmethod
    def _configured_key(cls) -> str:
        return settings.groq_api_key


class MistralClient(OpenAICompatibleClient):
    """Mistral. A wide per-minute lane, around 50,000 tokens."""

    name = "mistral"
    base_url = "https://api.mistral.ai/v1"
    model = "mistral-small-latest"
    fallback_models = ("open-mistral-nemo",)
    max_input_tokens = 40_000
    min_interval_seconds = 2.0

    @classmethod
    def _configured_key(cls) -> str:
        return settings.mistral_api_key


class OpenRouterClient(OpenAICompatibleClient):
    """OpenRouter. Last in the chain, because the daily cap is the tightest.

    Fifty requests a day on a free account, which is too few to carry a backfill
    and useful as a final overflow lane. Pinned to a GPT-OSS model because the
    free catalogue is inconsistent about `json_schema` and that family honours it.
    """

    name = "openrouter"
    base_url = "https://openrouter.ai/api/v1"
    model = "openai/gpt-oss-120b:free"
    fallback_models = ("meta-llama/llama-3.3-70b-instruct:free",)
    max_input_tokens = 30_000
    min_interval_seconds = 3.0

    @classmethod
    def _configured_key(cls) -> str:
        return settings.openrouter_api_key


class ChainClient(LLMClient):
    """Several providers in order, so one exhausted quota is not an outage.

    The point is arithmetic rather than redundancy. Each free tier is separately
    metered, so four of them is four allowances, and Loom's throughput ceiling
    has been a single provider's daily cap since the day it started reading
    filings.

    Two rules decide who takes a request:

    **A provider that cannot fit the input is skipped, not tried.** Per-minute
    token ceilings differ by an order of magnitude across these tiers, so
    sending a filing to a narrow lane buys a 429 and a retry cycle. Routing on
    the declared ceiling spends nothing to find that out.

    **An exhausted provider is dropped for the rest of the run.** Quota is a
    property of the key, so rediscovering it once per document would cost a
    round trip per document for the rest of a batch.

    Falling through is reserved for a provider being unusable. A provider that
    answered and produced output failing validation is not retried elsewhere:
    that is a prompt or schema problem, and asking a second model the same
    malformed question wastes a second allowance to get the same answer.
    """

    name = "chain"

    #: Which member actually answered, set as the chain falls through. Usage
    #: accounting needs it: without a `model` the recording call raised
    #: AttributeError on every run from the day the chain landed, which froze
    #: the usage table and hid a six day outage. The instrument has to survive
    #: the thing it measures.
    answered_by: LLMClient | None = None

    @property
    def model(self) -> str:
        if self.answered_by is not None:
            return f"{self.answered_by.name}/{self.answered_by.model}"
        return ",".join(c.name for c in self._clients) or "none"

    def __init__(self, clients: list[LLMClient] | None = None):
        super().__init__()
        self._clients = clients if clients is not None else _chain_from_settings()
        # Waiting out a rate limit only makes sense when there is nothing else
        # to try. With alternatives configured, a provider's own retry ladder is
        # pure latency: the first live run spent 83 seconds on Gemini backoff
        # before a provider that was ready could have answered immediately.
        # Patience is worth it only for the last provider in the chain.
        for client in self._clients[:-1]:
            if hasattr(client, "fail_fast"):
                client.fail_fast = True
        if not self._clients:
            raise LLMUnavailableError(
                "No LLM providers are configured. Set LLM_PROVIDERS and supply at "
                "least one key in secrets/ (gemini_api_key, cerebras_api_key, "
                "groq_api_key, mistral_api_key, openrouter_api_key)."
            )

    @property
    def available(self) -> bool:
        return any(c.available for c in self._clients)

    def _usable(self, system: str, user_content: str) -> list[LLMClient]:
        out = []
        for client in self._clients:
            if not client.available:
                continue
            accepts = getattr(client, "accepts", None)
            if accepts is not None and not accepts(user_content, system):
                continue
            out.append(client)
        return out

    def parse(self, *, system: str, user_content: str, schema: type[T],
              max_tokens: int = 16000) -> T | None:
        candidates = self._usable(system, user_content)
        if not candidates:
            # Distinguish "everything is out of quota" from "this input is too
            # large for any configured lane", because the fixes differ: wait
            # versus add a wider provider or shrink the input.
            sized = [c for c in self._clients if c.available]
            if sized:
                raise LLMUnavailableError(
                    f"No configured provider accepts an input of about "
                    f"{estimate_tokens(system) + estimate_tokens(user_content)} tokens. "
                    f"Add a provider with a wider per-minute limit, such as Cerebras."
                )
            raise LLMUnavailableError(
                "Every configured LLM provider is out of quota. They reset on "
                "rolling windows; retry later or add another provider."
            )

        last: Exception | None = None
        # Whether any provider actually answered. The interface draws a line
        # between the two outcomes and the chain has to preserve it: output that
        # cannot be validated returns None so one unreadable document does not
        # abort a batch, while every provider being out of quota affects
        # everything and raises. Falling through on both would collapse that
        # distinction and let a backfill grind silently through a dead chain.
        answered = False
        for client in candidates:
            try:
                result = client.parse(
                    system=system, user_content=user_content,
                    schema=schema, max_tokens=max_tokens,
                )
            except LLMUnavailableError as exc:
                last = exc
                logger.warning("%s unavailable (%s); trying the next provider.",
                               client.name, str(exc)[:120])
                continue
            finally:
                self.calls = sum(c.calls for c in self._clients)
                self.input_tokens = sum(c.input_tokens for c in self._clients)
                self.output_tokens = sum(c.output_tokens for c in self._clients)

            answered = True
            if result is not None:
                self.answered_by = client
                return result

            # None also falls through, and the first version of this did not.
            #
            # The argument for stopping was that a provider which answered and
            # returned unvalidatable output has a prompt or schema problem, so
            # asking a second model the same malformed question wastes an
            # allowance. That is sound in the abstract and wrong in practice: a
            # saturated free tier returns truncated output that fails validation
            # for exactly the same reason it would have failed to answer at all,
            # and from here the two are indistinguishable.
            #
            # Measured on the first live run: Gemini, rate limited, spent 83
            # seconds in backoff, returned unvalidatable output, and the chain
            # stopped without ever trying Cerebras or Groq — failing in the one
            # scenario it exists for. A real schema bug now costs a handful of
            # extra calls once and says so loudly in the log, which is the
            # cheaper way to be wrong.
            logger.warning(
                "%s returned no usable output; trying the next provider.", client.name
            )
            last = last or LLMUnavailableError(f"{client.name}: no usable output.")

        if not answered:
            raise LLMUnavailableError(
                f"All {len(candidates)} configured providers are unavailable. "
                f"Last: {str(last)[:200]}"
            )
        logger.error(
            "All %d usable providers returned unusable output. Last: %s",
            len(candidates), str(last)[:200],
        )
        return None

    @property
    def cost_usd(self) -> float:
        return sum(c.cost_usd for c in self._clients)

    def usage_summary(self) -> str:
        parts = [c.usage_summary() for c in self._clients if c.calls]
        return " | ".join(parts) if parts else "no provider calls"


def _chain_from_settings() -> list[LLMClient]:
    """Build the chain named by LLM_PROVIDERS, keeping only configured keys.

    Silently skipping a provider with no key is what lets the chain be declared
    once and work on an install that has one key or five, which is the same
    reasoning that keeps Loom runnable with no keys at all.
    """
    names = [n.strip().lower() for n in (settings.llm_providers or "").split(",") if n.strip()]
    if not names:
        names = [(settings.llm_provider or "gemini").lower()]

    chain: list[LLMClient] = []
    for name in names:
        client_class = _PROVIDERS.get(name)
        if client_class is None:
            logger.warning("Unknown LLM provider %r in LLM_PROVIDERS; skipping.", name)
            continue
        client = client_class()
        if client.available:
            chain.append(client)
        else:
            logger.info("Provider %s has no key configured; not in the chain.", name)
    return chain


_PROVIDERS: dict[str, type[LLMClient]] = {
    "gemini": GeminiClient,
    "anthropic": AnthropicClient,
    "cerebras": CerebrasClient,
    "groq": GroqClient,
    "mistral": MistralClient,
    "openrouter": OpenRouterClient,
}


def get_llm_client() -> LLMClient:
    """Single construction point. Adding a provider touches only this file.

    Returns a chain whenever LLM_PROVIDERS names more than one, because the
    throughput ceiling has always been one provider's daily cap and separate
    free tiers are separately metered. A single name still returns that provider
    directly, so an install configured the old way behaves exactly as before.
    """
    names = [n.strip().lower() for n in (settings.llm_providers or "").split(",") if n.strip()]
    if len(names) > 1:
        return ChainClient()

    provider = (names[0] if names else (settings.llm_provider or "gemini")).lower()
    client_class = _PROVIDERS.get(provider)
    if client_class is None:
        raise LLMUnavailableError(
            f"Unknown LLM provider {provider!r}. Valid options: {', '.join(_PROVIDERS)}."
        )
    return client_class()
