"""Several free tiers in order, so one exhausted quota is not an outage.

The point is arithmetic. Each provider's free tier is metered separately, so
four of them is four allowances, and Loom's throughput ceiling has been a single
provider's daily cap since it started reading filings.

What these tests mostly pin down is the routing, because the obvious design is
wrong. Free tiers advertise requests per day and are actually gated on tokens
per minute, and those ceilings differ by an order of magnitude: roughly 6,000
for Groq's small models against 60,000 for Cerebras. Loom's inputs differ by a
similar factor. Sending a filing down a narrow lane buys a 429 and a retry
cycle, so the chain declines rather than discovering it.
"""

from unittest import mock

import pytest
from pydantic import BaseModel

from app.engine.llm_client import (
    CerebrasClient,
    ChainClient,
    GroqClient,
    LLMClient,
    LLMUnavailableError,
    OpenAICompatibleClient,
    _validate,
    estimate_tokens,
)


class Shape(BaseModel):
    verdict: str


class Fake(LLMClient):
    """A provider that behaves however a test needs."""

    def __init__(self, name, *, ok=True, quota=False, ceiling=None, answer="yes"):
        super().__init__()
        self.name = name
        self._ok, self._quota, self._ceiling, self._answer = ok, quota, ceiling, answer
        self.attempts = 0

    @property
    def available(self):
        return self._ok

    def accepts(self, user_content, system=""):
        if self._ceiling is None:
            return True
        return estimate_tokens(system) + estimate_tokens(user_content) <= self._ceiling

    def parse(self, *, system, user_content, schema, max_tokens=16000):
        self.attempts += 1
        if self._quota:
            raise LLMUnavailableError(f"{self.name} out of quota")
        return schema(verdict=self._answer)


def _call(chain, content="x" * 400):
    return chain.parse(system="s", user_content=content, schema=Shape)


# ---- falling through -------------------------------------------------------


def test_an_exhausted_provider_hands_the_request_to_the_next():
    first, second = Fake("first", quota=True), Fake("second", answer="served")
    chain = ChainClient([first, second])

    assert _call(chain).verdict == "served"
    assert first.attempts == 1 and second.attempts == 1


def test_the_first_usable_provider_answers_and_the_rest_are_untouched():
    """Not load balancing. Order encodes which provider is preferred, and a
    later one is for when an earlier one cannot serve."""
    first, second = Fake("first", answer="served"), Fake("second")
    chain = ChainClient([first, second])

    assert _call(chain).verdict == "served"
    assert second.attempts == 0


def test_unusable_output_also_falls_through_to_the_next_provider():
    """This asserted the opposite, and the opposite was wrong.

    The original argument was that a provider which answered and returned
    unvalidatable output has a prompt or schema problem rather than a quota one,
    so trying a second model wastes an allowance on the same malformed question.
    Sound in the abstract. In practice a saturated free tier returns truncated
    output that fails validation for the same reason it would have failed to
    answer, and from the chain's position the two are indistinguishable.

    Measured on the first live run: Gemini, rate limited, spent 83 seconds in
    backoff, returned unvalidatable output, and the chain stopped without ever
    trying Cerebras or Groq — failing in the one scenario it exists for.
    """
    class Unvalidatable(Fake):
        def parse(self, **kwargs):
            self.attempts += 1
            return None

    first, second = Unvalidatable("first"), Fake("second", answer="served")
    chain = ChainClient([first, second])

    assert _call(chain).verdict == "served"
    assert first.attempts == 1 and second.attempts == 1


def test_every_provider_returning_nothing_yields_none_rather_than_raising():
    """A document that cannot be read must not abort a batch; that contract is
    older than the chain and the chain has to keep it.

    The companion case matters as much: when no provider *answered* at all the
    chain raises instead, because an exhausted chain affects every document and
    a backfill should stop rather than grind through it silently.
    """
    class Unvalidatable(Fake):
        def parse(self, **kwargs):
            self.attempts += 1
            return None

    chain = ChainClient([Unvalidatable("a"), Unvalidatable("b")])

    assert _call(chain) is None


def test_only_the_last_provider_in_the_chain_waits_out_a_rate_limit():
    """Patience is a property of position, not of the provider.

    A provider's retry ladder is worth a minute when nothing else can serve and
    is pure latency when the next provider is idle. The 83 seconds Gemini spent
    backing off on the first live run bought nothing, because Cerebras was ready.
    """
    first, middle, last = Fake("first"), Fake("middle"), Fake("last")
    ChainClient([first, middle, last])

    assert first.fail_fast and middle.fail_fast
    assert not last.fail_fast


# ---- routing on the limit that actually bites ------------------------------


def test_a_provider_too_narrow_for_the_input_is_skipped_rather_than_tried():
    narrow = Fake("narrow", ceiling=1_000)
    wide = Fake("wide", answer="served")
    chain = ChainClient([narrow, wide])

    assert _call(chain, "x" * 80_000).verdict == "served"
    assert narrow.attempts == 0, "a 429 that could be predicted should not be paid for"


def test_a_narrow_provider_still_serves_an_input_that_fits():
    """The skip is about the request, not the provider. Most of Loom's news
    corpus fits a narrow lane, and news is its largest source."""
    narrow = Fake("narrow", ceiling=1_000, answer="served")
    chain = ChainClient([narrow, Fake("wide")])

    assert _call(chain, "x" * 400).verdict == "served"


def test_groq_takes_a_news_batch_and_declines_a_filing():
    """The real ceilings, on the real shapes of input. A fifteen-item news batch
    is about a thousand tokens; a 10-K risk section is about twenty thousand."""
    groq = GroqClient(api_key="k")

    assert groq.accepts("x" * 4_000)
    assert not groq.accepts("x" * 80_000)
    assert CerebrasClient(api_key="k").accepts("x" * 80_000)


def test_an_oversized_input_is_reported_differently_from_an_empty_chain():
    """The two failures need different responses: wait for a reset, or add a
    wider provider. Collapsing them into one message loses that."""
    chain = ChainClient([Fake("narrow", ceiling=10)])
    with pytest.raises(LLMUnavailableError, match="wider per-minute"):
        _call(chain, "x" * 80_000)

    with pytest.raises(LLMUnavailableError, match="unavailable"):
        _call(ChainClient([Fake("gone", quota=True)]))


# ---- quota is a property of the key ---------------------------------------


def test_a_quota_failure_marks_the_provider_down_for_the_run():
    """The pipeline builds a fresh client per ticker, so per-instance state
    would rediscover an exhausted key once per document."""
    class Probe(OpenAICompatibleClient):
        name = "probe"
        base_url = "https://example.invalid/v1"

        @classmethod
        def _configured_key(cls):
            return "k"

    try:
        assert Probe().available
        Probe.exhausted = True
        assert not Probe().available, "exhaustion must outlive the instance"
    finally:
        Probe.exhausted = False


def test_a_per_minute_ceiling_does_not_mark_the_provider_down():
    """The regression that stopped the engine for six days.

    Cerebras answers an oversized filing with 429, `Retry-After: 60` and a body
    saying "Tokens per minute limit exceeded". That was classified as a spent
    allowance, which sets the flag on the class, and because nothing ever
    cleared it the provider stayed dead for the life of the process. The
    scheduler runs for days, so one oversized request took the provider out
    until somebody restarted the backend. It never recorded a single
    successful run.
    """
    import httpx

    class Probe(OpenAICompatibleClient):
        name = "probe"
        base_url = "https://example.invalid/v1"

        @classmethod
        def _configured_key(cls):
            return "k"

    limited = httpx.Response(
        429,
        headers={"retry-after": "60"},
        json={"message": "Tokens per minute limit exceeded - too many tokens processed.",
              "code": "token_quota_exceeded"},
        request=httpx.Request("POST", "https://example.invalid/v1/chat/completions"),
    )

    probe = Probe()
    probe.fail_fast = True  # as the chain sets it for every provider but the last
    try:
        with mock.patch.object(httpx.Client, "post", return_value=limited):
            with pytest.raises(LLMUnavailableError):
                probe.parse(system="s", user_content="u", schema=Shape)
        assert Probe().available, "a one minute ceiling must not disable the provider"
        assert Probe.exhausted is False
    finally:
        Probe.exhausted = False
        Probe.exhausted_at = 0.0


def test_a_spent_allowance_does_mark_the_provider_down():
    """The other half: a 429 that is genuinely the allowance, with no
    Retry-After and no per-minute wording, must still take the provider out so
    the chain stops asking it once per document."""
    import httpx

    class Probe(OpenAICompatibleClient):
        name = "probe"
        base_url = "https://example.invalid/v1"

        @classmethod
        def _configured_key(cls):
            return "k"

    spent = httpx.Response(
        429,
        json={"message": "You exceeded your current quota for the day."},
        request=httpx.Request("POST", "https://example.invalid/v1/chat/completions"),
    )
    probe = Probe()
    probe.fail_fast = True
    try:
        with mock.patch.object(httpx.Client, "post", return_value=spent):
            with pytest.raises(LLMUnavailableError):
                probe.parse(system="s", user_content="u", schema=Shape)
        assert not Probe().available, "a spent allowance is not worth re-asking"
    finally:
        Probe.exhausted = False
        Probe.exhausted_at = 0.0


def test_a_spent_allowance_clears_once_its_window_has_passed():
    """Free allowances reset on a rolling window, so the flag must not outlive
    it. Held forever, one bad hour costs a whole day of ingest."""
    import time as _time

    class Probe(OpenAICompatibleClient):
        name = "probe"
        base_url = "https://example.invalid/v1"
        quota_cooldown_seconds = 60

        @classmethod
        def _configured_key(cls):
            return "k"

    try:
        Probe.exhausted = True
        Probe.exhausted_at = _time.monotonic()
        assert not Probe().available, "still inside the window"

        Probe.exhausted_at = _time.monotonic() - 61
        assert Probe().available, "the window has passed; try it again"
        assert Probe.exhausted is False, "and the flag is cleared, not just ignored"
    finally:
        Probe.exhausted = False
        Probe.exhausted_at = 0.0


# ---- accounting ------------------------------------------------------------


def test_usage_is_reported_per_provider_and_summed():
    first, second = Fake("first", quota=True), Fake("second")
    first.calls, second.calls = 2, 3
    chain = ChainClient([first, second])

    _call(chain)

    assert chain.calls == 5
    assert "first" in chain.usage_summary() and "second" in chain.usage_summary()


def test_a_chain_with_nothing_configured_says_what_to_do():
    with pytest.raises(LLMUnavailableError, match="secrets/"):
        ChainClient([])


# ---- tolerating how models actually answer --------------------------------


def test_json_wrapped_in_a_markdown_fence_still_validates():
    """Models told to emit bare JSON fence it anyway often enough that
    stripping the fence is worth more than being strict."""
    assert _validate('```json\n{"verdict": "ok"}\n```', Shape).verdict == "ok"
    assert _validate('{"verdict": "ok"}', Shape).verdict == "ok"


def test_json_preceded_by_a_sentence_is_recovered():
    assert _validate('Here is the result: {"verdict": "ok"}', Shape).verdict == "ok"


def test_output_with_no_json_at_all_is_rejected_rather_than_guessed():
    assert _validate("I cannot answer that.", Shape) is None
    assert _validate("", Shape) is None
    assert _validate(None, Shape) is None
