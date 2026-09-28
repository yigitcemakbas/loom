"""The reader benchmark: does giving a decision-maker Loom improve decisions?

The unit is not a signal and not a portfolio. It is a *decision made by a reader
who was given something to read*, and the causal chain the benchmark has to
preserve is information -> reader reasoning -> decision -> outcome. So the thing
that varies between arms is only what the reader was handed, and everything else
about the task is held identical by construction rather than by care.

The design can return "Loom helps", "Loom does nothing", or "Loom actively
harms", and nothing here is arranged to favour the first. The two measures most
likely to embarrass the product are given first-class treatment: `added_loss`,
which is what a research system does when it makes a poor decision more
confident, and the anchoring test, which asks whether a reader handed the verdict
follows it even where the underlying evidence points elsewhere.

What the reader is asked to do is manage money, not to rate Loom. The prompt
never mentions Loom, never says an experiment is running, and never hints that
the research should be trusted or doubted.
"""
from __future__ import annotations

import json
import pathlib
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Optional

import spec

HERE = pathlib.Path(__file__).parent
DECISIONS = HERE / "decisions"
PACKETS = HERE / "packet_cache"

PROMPT_VERSION = "readerbench-2026-09-28.1"
CAPITAL = 1000


# --------------------------------------------------------------- the task

# Identical in every arm, down to the wording. The only difference between a
# control reader and a treatment reader is the research pasted above this.
TASK = """You are managing a portfolio of {capital} units of capital as at the close on {as_of}.

You may take positions in any of the {n} companies described above, or hold cash.
Your positions will be held for the next {sessions} trading sessions and you will
not be able to change them, and you will receive no further information before
then.

Decide freely. You may concentrate or diversify, size positions as you see fit,
hold as much or as little cash as you judge right, and take short positions where
you think they are warranted. Long weights plus short weights must not exceed 1.0
in total; whatever you do not allocate is cash.

Report only the positions you actually take. Anything you do not list is not held.

Answer with a single JSON object and nothing else. No preamble, no explanation
outside the JSON, no markdown fence:

{{
  "positions": [
    {{
      "ticker": "AAA",
      "weight": 0.10,
      "direction": "long",
      "thesis": "one or two sentences on why you hold this",
      "evidence": ["phrases copied exactly from the material above that drove this decision"],
      "counterargument": "the strongest case against this position",
      "confidence": 0.62
    }}
  ],
  "cash": 0.30,
  "reasoning": "two or three sentences on how you built the portfolio as a whole"
}}

Rules for the fields:
- "direction" is "long" or "short".
- "weight" is a fraction of the whole portfolio, greater than 0.
- "confidence" is your honest probability from 0 to 1 that this position beats
  the market over the holding period. 0.5 means a coin flip. Be calibrated
  rather than enthusiastic.
- "evidence" must contain phrases copied verbatim from the material above. If
  nothing in that material drove the decision, leave the list empty rather than
  inventing a citation.
- "cash" is the fraction you deliberately leave unallocated.

Do not use any tools. Do not read files, run commands, or search for anything.
Decide with the material you have been given and reply with the JSON."""


def decision_prompt(*, packet: str, persona: str, as_of: date, sessions: int,
                    n_names: int) -> str:
    """The whole of what a reader sees: their own disposition, the research, the task."""
    return (
        f"{spec.PERSONAS_BY_KEY[persona].system}\n\n"
        f"{packet}\n\n"
        f"{TASK.format(capital=CAPITAL, as_of=as_of.isoformat(), n=n_names, sessions=sessions)}"
    )


# --------------------------------------------------------------- parsing

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_decision(text: str) -> Optional[dict]:
    """Pull the JSON object out of an agent's reply, tolerantly.

    Tolerant because a reader that wrapped its answer in a fence or added a
    sentence has still made a decision, and discarding it would silently bias
    the sample toward whichever arm happened to produce tidier output.
    """
    if not text:
        return None
    candidates = []
    fenced = _FENCE.search(text)
    if fenced:
        candidates.append(fenced.group(1))
    start = text.find("{")
    if start >= 0:
        depth, end = 0, None
        for i, ch in enumerate(text[start:], start):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    break
        if end:
            candidates.append(text[start:end])
    candidates.append(text)
    for raw in candidates:
        try:
            out = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(out, dict) and isinstance(out.get("positions"), list):
            return out
    return None


def normalise(decision: dict) -> dict:
    """Coerce a decision into the shape the scorer expects, without judging it.

    Overrun gross exposure is scaled back rather than rejected: a reader that
    allocated 1.4 has made a real decision about relative sizing, and throwing
    the book away would drop the most aggressive readers from the sample, which
    is exactly the population the `added_loss` measure exists to observe.
    """
    positions = []
    for p in decision.get("positions") or []:
        try:
            ticker = str(p.get("ticker", "")).strip().upper()
            weight = float(p.get("weight") or 0)
        except (TypeError, ValueError):
            continue
        if not ticker or weight <= 0:
            continue
        direction = str(p.get("direction", "long")).strip().lower()
        if direction not in ("long", "short"):
            direction = "long"
        conf = p.get("confidence")
        try:
            conf = float(conf)
        except (TypeError, ValueError):
            conf = None
        if conf is not None and not 0.0 <= conf <= 1.0:
            conf = None
        ev = [str(x) for x in (p.get("evidence") or []) if str(x).strip()]
        positions.append({
            "ticker": ticker, "weight": weight, "direction": direction,
            "thesis": str(p.get("thesis") or ""), "evidence": ev,
            "counterargument": str(p.get("counterargument") or ""),
            "confidence": conf,
        })

    gross = sum(p["weight"] for p in positions)
    scaled = gross > 1.0
    if scaled and gross > 0:
        for p in positions:
            p["weight"] /= gross
    return {
        "positions": positions,
        "cash": max(0.0, 1.0 - sum(p["weight"] for p in positions)),
        "reasoning": str(decision.get("reasoning") or ""),
        "gross_before_scaling": round(gross, 4),
        "scaled_back": scaled,
    }


def weights(decision: dict) -> dict[str, float]:
    """Signed exposure per name."""
    out: dict[str, float] = {}
    for p in decision["positions"]:
        out[p["ticker"]] = p["weight"] * (1 if p["direction"] == "long" else -1)
    return out


# --------------------------------------------------------------- the record


@dataclass
class Cell:
    """One reader, one arm, one date. The atom of the experiment."""
    as_of: date
    persona: str
    arm: str
    seed: int
    sessions: int = 63
    model: str = "haiku"

    @property
    def name(self) -> str:
        return f"{self.as_of}__{self.persona}__{self.arm}__seed{self.seed}"

    @property
    def cell_key(self) -> tuple:
        """What a paired comparison holds fixed: everything except the arm."""
        return (self.as_of.isoformat(), self.persona, self.seed, self.sessions)


def matrix() -> list[Cell]:
    """The experiment matrix, ordered so that any prefix is a complete design.

    Whole (date, persona) blocks finish before the next begins, because a cell
    missing its control contributes nothing to a paired treatment effect and a
    half-finished block is spent budget that answers no registered question.
    """
    cells: list[Cell] = []
    for w in spec.READER_WINDOWS:
        for p in spec.PERSONAS:
            for s in spec.SEEDS[:1]:
                for arm in spec.CORE_READER_ARMS:
                    cells.append(Cell(w.decide, p.key, arm, s, w.sessions))
    # The fourth arm is diagnostic rather than primary, so it queues behind the
    # three that carry the registered hypotheses.
    for w in spec.READER_WINDOWS:
        for p in spec.PERSONAS:
            cells.append(Cell(w.decide, p.key, "verdict_only", 1, w.sessions))
    # A second seed on the earliest date, for the consistency measure. Dates are
    # preferred to seeds under a budget, so this is last.
    for p in spec.PERSONAS:
        for arm in spec.CORE_READER_ARMS:
            cells.append(Cell(spec.READER_WINDOWS[0].decide, p.key, arm, 2,
                              spec.READER_WINDOWS[0].sessions))
    return cells


def packet_for(cell: Cell) -> tuple[str, int, dict]:
    f = PACKETS / f"t1__{cell.as_of}__{cell.arm}__u1.json"
    d = json.loads(f.read_text())
    return d["body"], d["n_names"], d


def save(cell: Cell, *, raw: str, decision: Optional[dict], packet_meta: dict) -> pathlib.Path:
    """Write the decision record once. Never rewritten after outcomes are known.

    The record holds what the reader was given and what it decided, and nothing
    about what happened next. Outcomes are appended to a separate file, so a
    decision cannot be quietly reinterpreted once its result is visible.
    """
    DECISIONS.mkdir(exist_ok=True)
    path = DECISIONS / f"{cell.name}.json"
    if path.exists():
        return path
    body = {
        "cell": cell.name, "as_of": cell.as_of.isoformat(), "persona": cell.persona,
        "arm": cell.arm, "seed": cell.seed, "sessions": cell.sessions,
        "model": cell.model, "prompt_version": PROMPT_VERSION,
        "packet_fingerprint": packet_meta.get("fingerprint"),
        "packet_names": packet_meta.get("n_names"),
        "packet_verdicts": packet_meta.get("verdicts"),
        "provenance": {k: packet_meta.get(k) for k in
                       ("newest_signal", "newest_bar", "newest_fact", "as_of")},
        "raw_reply": raw,
        "decision": normalise(decision) if decision else None,
        "parsed": decision is not None,
    }
    path.write_text(json.dumps(body, indent=1))
    return path
