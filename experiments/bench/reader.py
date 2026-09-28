"""The reader agent: one model, one persona, one packet, one allocation.

The agent is never told which arm it is in, nor that an experiment is running.
Demand effects are the obvious way to get a flattering result out of a design
like this, and the cheapest defence is that the treatment is invisible.
"""
from __future__ import annotations

import json
import random
import time
from typing import Optional

from pydantic import BaseModel, Field

from app.engine.llm_client import GeminiClient, LLMUnavailableError

import spec


class Position(BaseModel):
    ticker: str
    action: str = Field(description="one of: buy, short, pass")
    weight: float = Field(description="fraction of the portfolio, 0 to 1; 0 for a pass")
    conviction: float = Field(
        description="your probability from 0 to 1 that this position beats the "
                    "market over the holding period. 0.5 means a coin flip.")
    thesis: str = Field(description="one sentence, why")
    evidence_cited: list[str] = Field(
        default_factory=list,
        description="verbatim phrases copied exactly from the research you were "
                    "given that drove this decision. Empty if none did.")


class Allocation(BaseModel):
    positions: list[Position]
    cash_reasoning: str = Field(description="why you left the cash you left")


_INSTRUCTIONS = """You are allocating a portfolio for the next {sessions} trading sessions.

You have {n} companies to consider. For every one of them, return a position:
  buy   - you want to own it
  short - you want to be short it
  pass  - you want neither, weight 0

Rules:
  - Long weights plus short weights must not exceed 1.0 in total. Whatever you
    do not allocate is cash.
  - Give a conviction for every position: your honest probability that it beats
    the market over the period. Be calibrated, not enthusiastic. If you do not
    know, 0.5 is the correct answer.
  - In evidence_cited, copy exact phrases from the research above that drove the
    decision. Copy them verbatim. If nothing in the research drove it, leave it
    empty rather than inventing a citation.
  - You will not receive any further information. Decide with what you have.
"""


def _order(tickers_block: str, seed: int) -> str:
    """Re-order the company blocks in the packet.

    A prompt is read most carefully at the top, so position in the list is a
    prior. Varying it per seed is what separates a real effect from an artefact
    of whichever company happened to be printed first.
    """
    head, _, rest = tickers_block.partition("\n\n---\n\n")
    blocks = rest.split("\n\n---\n\n") if rest else []
    rnd = random.Random(f"order-{seed}")
    rnd.shuffle(blocks)
    return "\n\n---\n\n".join([head] + blocks)


def _client(model: str) -> GeminiClient:
    c = GeminiClient()
    c.model = model
    # No silent substitution to a *different* model: a book attributed to 3.5
    # that was answered by 3.6 would corrupt the only model-family comparison in
    # the run. Set to the same model rather than empty, because the client
    # indexes this tuple and an empty one raises from inside the retry loop.
    c._FALLBACK_MODELS = (model,)
    return c


def read(*, packet: str, persona: str, seed: int, sessions: int, n_names: int,
         model: str = spec.READER_MODEL) -> Optional[dict]:
    """Produce one book, or None if the provider refused.

    Returns a plain dict so a book survives a change to this module: the run is
    long and the files outlive the code that wrote them.
    """
    system = spec.PERSONAS_BY_KEY[persona].system
    body = _order(packet, seed)
    user = body + "\n\n" + _INSTRUCTIONS.format(sessions=sessions, n=n_names)

    client = _client(model)
    if not client.available:
        raise LLMUnavailableError("no Gemini key configured")

    started = time.time()
    try:
        out = client.parse(system=system, user_content=user,
                           schema=Allocation, max_tokens=24000)
    except LLMUnavailableError:
        raise
    except Exception as exc:                      # a provider failure, not a bug
        return {"error": f"{type(exc).__name__}: {exc}"}
    latency = time.time() - started

    if out is None:
        return {"error": "provider returned nothing parseable"}

    return {
        "positions": [p.model_dump() for p in out.positions],
        "cash_reasoning": out.cash_reasoning,
        "model": model,
        "latency_seconds": round(latency, 2),
        "input_tokens": client.input_tokens,
        "output_tokens": client.output_tokens,
        "calls": client.calls,
    }
