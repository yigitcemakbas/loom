"""Retry and fallback behaviour, with no network.

This is the code that decides whether a batch survives a bad ten minutes on a
free tier, and its failure modes were all found in production rather than in
tests: an SDK-internal retry that multiplied against this one into a two-hour
hang, and a read timeout that skipped the retry path entirely. Both are pinned
here.
"""

import httpx
import pytest
from pydantic import BaseModel

from app.config import settings
from app.engine.llm_client import GeminiClient, LLMUnavailableError


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch):
    """Pacing is a real wall-clock sleep between requests. Left on, this file
    alone spent 90 seconds sleeping against a fake client."""
    monkeypatch.setattr(settings, "llm_min_call_interval_seconds", 0)
    GeminiClient._last_call_at = 0.0


class _Schema(BaseModel):
    value: int


class _FakeModels:
    """Stands in for `client.models`, counting calls and raising to order."""

    def __init__(self, error: Exception | None, succeed_on: int | None = None):
        self.error = error
        self.succeed_on = succeed_on
        self.calls = 0

    def generate_content(self, **_kwargs):
        self.calls += 1
        if self.succeed_on is not None and self.calls >= self.succeed_on:
            return "ok"
        raise self.error


def _client(error: Exception | None, *, succeed_on: int | None = None, attempts: int = 2):
    client = GeminiClient()
    client._TRANSIENT_ATTEMPTS = attempts
    client._BACKOFF_BASE_SECONDS = 0.001
    client._RATE_LIMIT_BACKOFF_SECONDS = 0.001
    fake = _FakeModels(error, succeed_on)
    client._client = type("FakeClient", (), {"models": fake})()
    return client, fake


def _generate(client):
    return client._generate(system="s", user_content="u", schema=_Schema, max_tokens=10)


def test_read_timeout_is_retried_across_every_model():
    """A timeout is the most transient failure there is, but was once the least
    tolerated: server errors got three attempts per model while a timeout
    aborted the call outright."""
    client, fake = _client(httpx.ReadTimeout("simulated"), attempts=2)

    with pytest.raises(LLMUnavailableError):
        _generate(client)

    # 2 attempts on each of the primary model plus two fallbacks.
    assert fake.calls == 6


def test_a_recovered_timeout_returns_normally():
    """The point of retrying is that the second attempt often works."""
    client, fake = _client(httpx.ReadTimeout("simulated"), succeed_on=2, attempts=3)

    assert _generate(client) == "ok"
    assert fake.calls == 2


def test_network_failure_message_does_not_blame_quota():
    """The three transient failures need different messages because they need
    different responses: wait, retry, or check the network."""
    client, _ = _client(httpx.ConnectError("simulated"))

    with pytest.raises(LLMUnavailableError) as caught:
        _generate(client)

    message = str(caught.value)
    assert "network layer" in message
    assert "quota" not in message


def test_pacing_spaces_consecutive_calls(monkeypatch):
    """Pacing is what keeps a batch under the per-minute cap. Measured over a
    real backfill, 14 of 16 transient failures were rate limits, each costing
    roughly a minute of backoff before the document got any verdict at all."""
    monkeypatch.setattr(settings, "llm_min_call_interval_seconds", 0.25)
    GeminiClient._last_call_at = 0.0

    slept: list[float] = []
    monkeypatch.setattr("app.engine.llm_client.time.sleep", slept.append)
    monkeypatch.setattr("app.engine.llm_client.time.monotonic", lambda: 1000.0)

    GeminiClient._pace()   # first call sets the clock, no wait worth noting
    GeminiClient._pace()   # immediately after, must be asked to wait

    assert slept and slept[-1] == pytest.approx(0.25, abs=0.01)


def test_pacing_can_be_disabled():
    """A paid tier has no per-minute cap worth pacing around, and throughput
    matters more there than politeness."""
    settings_interval = settings.llm_min_call_interval_seconds
    try:
        settings.llm_min_call_interval_seconds = 0
        GeminiClient._last_call_at = 0.0
        GeminiClient._pace()  # must return immediately, not raise or block
    finally:
        settings.llm_min_call_interval_seconds = settings_interval


def test_non_transport_errors_are_not_swallowed():
    """Only transient classes get the retry loop. A programming error must
    surface immediately rather than be retried six times and relabelled."""
    client, fake = _client(ValueError("bad schema"))

    with pytest.raises(ValueError):
        _generate(client)

    assert fake.calls == 1
