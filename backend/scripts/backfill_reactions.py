"""Attach a market-reaction judgement to findings extracted before it existed.

Loom stores three qualitative tags on every finding: direction, magnitude and
horizon. The horizon is what the timeframe buttons weight, and `brief.py`
refuses to call a company quiet when too few of its findings carry the tags at
all (`_MIN_ASSESSED_SHARE`), because reporting an unread company as calm is the
most misleading thing it could do.

Everything extracted before the market-reaction fields were added carries none
of the three. Those findings are not wrong, they are mute: they sit in the
corpus diluting every horizon toward the middle weight and pushing companies
under the assessed-share floor.

What this does NOT do is re-read the source filing. The summary and the
verbatim quote already on the row are what the original extraction distilled
from that filing, and they are what a judgement of "how long does this take to
play out" actually rests on. Re-reading a 10-K to re-derive one tag would cost
several hundred times the tokens for the same answer, and on a free tier that
is the difference between a backfill that finishes and one that dies of quota.

Findings are judged in batches, because pacing is per call: 122 findings one at
a time is 122 paced calls, and eight to a call is fifteen. The model echoes back
the index it was given and anything that does not line up is discarded rather
than guessed at, since a misaligned batch would silently attach one company's
judgement to another's finding.

Usage:
    python -m scripts.backfill_reactions --dry-run
    python -m scripts.backfill_reactions
    python -m scripts.backfill_reactions --limit 40 NVDA AMD
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

from pydantic import BaseModel, Field
from sqlalchemy import select

from app.db.session import SessionLocal
from app.engine.llm_client import LLMUnavailableError, get_llm_client
from app.engine.prompts.market_reaction import (
    MARKET_REACTION_RULES,
    Direction,
    Horizon,
    Magnitude,
)
from app.models.company import Company
from app.models.document import RawDocument
from app.models.signal import Signal, SignalType

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("backfill_reactions")

# SENTIMENT_SHIFT is deliberately absent. It carries a sentiment score rather
# than a reaction and never had these fields, including on rows written today,
# so filling it would not be a backfill, it would be a new claim about a kind
# of finding Loom has never made one about.
BACKFILLABLE = (
    SignalType.NEW_RISK_FACTOR,
    SignalType.NOTABLE_QUOTE,
    SignalType.GUIDANCE_CHANGE,
    SignalType.QOQ_ANOMALY,
    SignalType.EMERGING_PATTERN,
)

# Eight is where the prompt still reads as a list of separate judgements. At
# twenty the model measurably starts to harmonise them, giving one company's
# findings a single direction because they arrived together.
BATCH_SIZE = 8

BACKFILL_TAG = "reaction_backfill"


class IndexedReaction(BaseModel):
    """One judgement, tied to the finding it belongs to by the index given."""

    index: int = Field(description="The finding number exactly as shown in the input.")
    direction: Direction = Field(description="The likely directional market reaction to this finding.")
    magnitude: Magnitude = Field(
        description="How material this looks relative to how markets typically react to this type of disclosure."
    )
    horizon: Horizon = Field(
        description="near_term: the kind of thing usually priced in within days. "
        "multi_quarter: plays out over the next few quarters. "
        "structural: a multi-year shift in the business."
    )
    rationale: str = Field(
        description="One sentence grounding the above in this specific finding. "
        "Never a percentage, price target, or dollar figure."
    )


class BatchedReactions(BaseModel):
    reactions: list[IndexedReaction] = Field(
        description="One entry per finding given, in any order, each carrying its own index."
    )


SYSTEM = f"""You are characterizing how markets typically read corporate disclosures.

You will be given several findings that an earlier pass extracted from company
filings and earnings calls. Each has a number, the kind of finding it is, a one
line summary, and where available the verbatim sentence it came from.

Judge each finding ON ITS OWN. They may come from the same company and the same
filing; that does not make them one story, and findings from one document
routinely point in opposite directions. Return one entry per finding, echoing
back the number it was given.

Judge only from the text shown. Do not use anything you may know about what
happened to these companies afterwards: the tag is a characterization of the
disclosure as it read at the time, and hindsight would make it worthless for
measuring whether Loom's judgements hold up.

{MARKET_REACTION_RULES}"""


def _render(findings: list[Signal], docs: dict) -> str:
    lines = []
    for i, signal in enumerate(findings, start=1):
        doc = docs.get(signal.source_document_id)
        where = doc.doc_subtype or doc.source_name if doc else "company disclosure"
        lines.append(f"FINDING {i}")
        lines.append(f"  kind: {signal.signal_type.value}")
        lines.append(f"  source: {where}")
        lines.append(f"  summary: {signal.summary}")
        if signal.evidence_quote:
            lines.append(f'  verbatim: "{signal.evidence_quote.strip()}"')
        lines.append("")
    return "\n".join(lines)


def align(batch: list, reactions: list[IndexedReaction]) -> tuple[list[tuple], list[int]]:
    """Pair each judgement with the finding it was made about, dropping any it
    cannot pair confidently.

    The model is asked to echo the number it was given, which is the only thing
    tying a judgement to a finding once the batch is unpacked. An index outside
    the batch, or the same index twice, means the response is not aligned with
    what was asked. Dropping it loses one judgement and costs one more run;
    trusting it writes one company's judgement onto another company's finding,
    which is silent and permanent. Returns the pairs it trusts and the indexes
    it refused.
    """
    pairs: list[tuple] = []
    refused: list[int] = []
    seen: set[int] = set()
    for reaction in reactions:
        position = reaction.index - 1
        if position < 0 or position >= len(batch) or reaction.index in seen:
            refused.append(reaction.index)
            continue
        seen.add(reaction.index)
        pairs.append((batch[position], reaction))
    return pairs, refused


def _apply(signal: Signal, reaction: IndexedReaction, *, now: datetime) -> None:
    signal.market_direction = reaction.direction
    signal.market_magnitude = reaction.magnitude
    signal.market_horizon = reaction.horizon
    # Live extraction puts the rationale sentence in `detail`, so these rows
    # end up shaped like every other one. Never overwritten: a finding that
    # already carries a detail has something of its own to say.
    if not signal.detail:
        signal.detail = reaction.rationale
    # Recorded so the evaluation harness can tell a judgement made at
    # extraction time from one made months later. They are not the same
    # evidence even when they are the same words, and a backtest that treats
    # them as interchangeable is measuring its own hindsight.
    meta = dict(signal.signal_metadata or {})
    meta[BACKFILL_TAG] = {"at": now.isoformat(), "method": "finding_text_only"}
    signal.signal_metadata = meta


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Attach direction/magnitude/horizon to findings that lack them."
    )
    parser.add_argument("tickers", nargs="*", help="Limit to these companies. Default: all.")
    parser.add_argument("--limit", type=int, default=None, help="Stop after this many findings.")
    parser.add_argument("--dry-run", action="store_true", help="Report what would run, call nothing.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        query = (
            select(Signal)
            .where(Signal.market_horizon.is_(None))
            .where(Signal.signal_type.in_(BACKFILLABLE))
            .order_by(Signal.company_id, Signal.occurred_at)
        )
        if args.tickers:
            upper = [t.upper() for t in args.tickers]
            query = query.join(Company).where(Company.ticker.in_(upper))

        pending = list(db.execute(query).unique().scalars())
        if args.limit is not None:
            pending = pending[: args.limit]

        if not pending:
            logger.info("Nothing to backfill: every eligible finding already carries a reaction.")
            return 0

        doc_ids = {s.source_document_id for s in pending if s.source_document_id}
        docs = {
            d.id: d
            for d in db.execute(select(RawDocument).where(RawDocument.id.in_(doc_ids))).scalars()
        } if doc_ids else {}

        batches = [pending[i : i + BATCH_SIZE] for i in range(0, len(pending), BATCH_SIZE)]
        logger.info(
            "%d findings without a market reaction, in %d batches of up to %d.",
            len(pending), len(batches), BATCH_SIZE,
        )
        if args.dry_run:
            by_type: dict[str, int] = {}
            for signal in pending:
                by_type[signal.signal_type.value] = by_type.get(signal.signal_type.value, 0) + 1
            for name, count in sorted(by_type.items(), key=lambda kv: -kv[1]):
                logger.info("  %-22s %d", name, count)
            return 0

        client = get_llm_client()
        if not client.available:
            logger.error("No LLM client configured. Set GEMINI_API_KEY in backend/.env.")
            return 1

        filled = skipped = 0
        started = time.monotonic()

        for number, batch in enumerate(batches, start=1):
            try:
                result = client.parse(
                    system=SYSTEM,
                    user_content=_render(batch, docs),
                    schema=BatchedReactions,
                    max_tokens=4000,
                )
            except LLMUnavailableError as exc:
                # Applies to everything remaining, so stop. Batches already
                # committed stay done and a later run picks up from there.
                logger.error("Stopping at batch %d/%d: %s", number, len(batches), exc)
                break
            except Exception:
                logger.exception("Batch %d failed", number)
                skipped += len(batch)
                continue

            if result is None:
                logger.warning("Batch %d returned nothing usable.", number)
                skipped += len(batch)
                continue

            now = datetime.now(timezone.utc)
            pairs, refused = align(batch, result.reactions)
            for index in refused:
                logger.warning("Batch %d: discarding unusable index %d.", number, index)
            for signal, reaction in pairs:
                _apply(signal, reaction, now=now)
                filled += 1

            missing = len(batch) - len(pairs)
            if missing:
                logger.warning("Batch %d: %d findings came back unjudged.", number, missing)
                skipped += missing

            # Committed per batch rather than at the end, so an exhausted quota
            # or a killed process keeps everything judged so far.
            db.commit()
            logger.info("  batch %2d/%d  %d judged", number, len(batches), len(pairs))

        remaining = db.execute(
            select(Signal)
            .where(Signal.market_horizon.is_(None))
            .where(Signal.signal_type.in_(BACKFILLABLE))
        ).scalars().all()

        logger.info(
            "Filled %d, skipped %d, in %.0fs (%s). %d eligible findings still unjudged.",
            filled, skipped, time.monotonic() - started, client.usage_summary(), len(remaining),
        )
        if remaining:
            logger.info("Run again to continue; findings already judged are skipped.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
