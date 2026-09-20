"""The fast path: score an event against what Loom already believed.

Everything here is arithmetic and string matching over a prior that was
computed in advance. There is no model call, no database write, and no network
access on this path, and those are requirements rather than optimisations. The
window in which a reaction to a filing is worth anything is minutes, and the
engine's model calls are paced in seconds each; a design that thinks after the
event arrives is too late no matter how good the thinking is.

So the expensive judgement happened earlier (`engine/prior.py`) and produced
watch items with literal keywords attached. What happens here is: match the
event text against those keywords, measure any numeric surprise against stored
consensus, and weight the result by how the market was positioned. All of that
is microseconds.

What this module will not do is claim a price move. It says which of Loom's
standing expectations an event confirms, how hard, and which way, leaving the
trade to the reader.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

# How much each severity contributes when a watch item is confirmed.
_SEVERITY_WEIGHT = {"minor": 0.5, "moderate": 1.0, "major": 2.0}

# An EPS or revenue surprise smaller than this is noise, not news.
MEANINGFUL_SURPRISE_PERCENT = 2.0

# A crowded short amplifies an upside surprise, because covering means buying.
CROWDED_SHORT_AMPLIFIER = 1.35

# When the market has already moved hard on a topic, a confirmation of it is
# worth less, not more.
ALREADY_PRICED_DISCOUNT = 0.5

# Score above which the event is worth surfacing at all.
NOTABLE_SCORE = 1.0


@dataclass
class MarketEvent:
    """Something that just happened, in the plainest possible form.

    Deliberately not an ORM object. The fast path must be callable from a
    webhook, a poller, or a test with no database attached.
    """

    ticker: str
    kind: str                      # "earnings" | "filing" | "news"
    occurred_at: datetime
    text: str = ""                 # headline, press release, or filing body
    eps_actual: Optional[float] = None
    revenue_actual: Optional[float] = None
    item_codes: list[str] = field(default_factory=list)   # 8-K items, e.g. ["5.02"]
    source_url: Optional[str] = None


@dataclass
class MatchedExpectation:
    topic: str
    direction: str
    severity: str
    why_it_matters: str
    matched_keywords: list[str]
    already_priced: bool = False


@dataclass
class EventAssessment:
    """What Loom makes of an event, given what it already believed."""

    ticker: str
    kind: str
    score: float
    direction: str                 # "positive" | "negative" | "neutral"
    headline: str
    matches: list[MatchedExpectation] = field(default_factory=list)
    eps_surprise_percent: Optional[float] = None
    revenue_surprise_percent: Optional[float] = None
    amplifiers: list[str] = field(default_factory=list)
    prior_generated_at: Optional[datetime] = None
    elapsed_ms: float = 0.0

    @property
    def is_notable(self) -> bool:
        return self.score >= NOTABLE_SCORE


def _surprise_percent(actual: Optional[float], estimate: Optional[float]) -> Optional[float]:
    if actual is None or estimate in (None, 0):
        return None
    return round((actual - estimate) / abs(estimate) * 100, 2)


def _keyword_hits(text_lower: str, keywords: list[str]) -> list[str]:
    """Which of an item's keywords appear in the event text.

    Word-boundary matching rather than a bare substring test, so that "ai" does
    not fire on "chain" and "china" does not fire on "machinery". Multi-word
    phrases are matched literally.
    """
    hits = []
    for keyword in keywords:
        if not keyword:
            continue
        pattern = r"\b" + re.escape(keyword) + r"\b"
        if re.search(pattern, text_lower):
            hits.append(keyword)
    return hits


def assess(event: MarketEvent, prior) -> EventAssessment:
    """Score one event against one standing prior. Pure, and fast by construction.

    `prior` is a CompanyPrior row or anything with the same attributes; it is
    duck-typed so this module stays callable without the ORM.
    """
    started = datetime.now()

    if prior is None:
        return EventAssessment(
            ticker=event.ticker,
            kind=event.kind,
            score=0.0,
            direction="neutral",
            headline=(
                f"No standing prior for {event.ticker}, so this event cannot be "
                f"judged against expectations."
            ),
        )

    text_lower = (event.text or "").lower()
    priced_topics = {
        (entry.get("topic") or "").lower()
        for entry in (prior.already_priced or [])
    }

    score = 0.0
    directional = 0.0
    matches: list[MatchedExpectation] = []

    for item in prior.watch_items or []:
        hits = _keyword_hits(text_lower, item.get("keywords") or [])
        if not hits:
            continue

        weight = _SEVERITY_WEIGHT.get(item.get("severity", "moderate"), 1.0)
        priced = (item.get("topic") or "").lower() in priced_topics
        if priced:
            weight *= ALREADY_PRICED_DISCOUNT

        score += weight
        direction = item.get("direction_if_confirmed", "neutral")
        if direction == "positive":
            directional += weight
        elif direction == "negative":
            directional -= weight

        matches.append(
            MatchedExpectation(
                topic=item.get("topic", "unknown"),
                direction=direction,
                severity=item.get("severity", "moderate"),
                why_it_matters=item.get("why_it_matters", ""),
                matched_keywords=hits,
                already_priced=priced,
            )
        )

    expectations = prior.expectations or {}
    eps_surprise = _surprise_percent(event.eps_actual, expectations.get("eps_estimate"))
    revenue_surprise = _surprise_percent(
        event.revenue_actual, expectations.get("revenue_estimate")
    )

    for surprise in (eps_surprise, revenue_surprise):
        if surprise is None or abs(surprise) < MEANINGFUL_SURPRISE_PERCENT:
            continue
        # Capped: a 400% "surprise" against a near-zero estimate is an artefact
        # of the denominator, not a signal of that magnitude.
        weight = min(abs(surprise) / 10.0, 2.0)
        score += weight
        directional += weight if surprise > 0 else -weight

    amplifiers: list[str] = []
    positioning = prior.positioning or {}
    if positioning.get("short_crowded") and directional > 0:
        score *= CROWDED_SHORT_AMPLIFIER
        directional *= CROWDED_SHORT_AMPLIFIER
        amplifiers.append(
            f"crowded short at {positioning.get('days_to_cover')} days to cover, "
            f"so an upside surprise forces covering"
        )

    direction = "positive" if directional > 0.25 else "negative" if directional < -0.25 else "neutral"

    elapsed_ms = (datetime.now() - started).total_seconds() * 1000
    return EventAssessment(
        ticker=event.ticker,
        kind=event.kind,
        score=round(score, 2),
        direction=direction,
        headline=_headline(event, matches, eps_surprise, direction, score),
        matches=matches,
        eps_surprise_percent=eps_surprise,
        revenue_surprise_percent=revenue_surprise,
        amplifiers=amplifiers,
        prior_generated_at=getattr(prior, "generated_at", None),
        elapsed_ms=round(elapsed_ms, 3),
    )


def _headline(event, matches, eps_surprise, direction, score) -> str:
    """One sentence, stating what was expected and what arrived."""
    if not matches and eps_surprise is None:
        return f"{event.ticker}: nothing in this event matches what Loom was watching for."

    parts = []
    if eps_surprise is not None and abs(eps_surprise) >= MEANINGFUL_SURPRISE_PERCENT:
        verb = "beat" if eps_surprise > 0 else "missed"
        parts.append(f"EPS {verb} consensus by {abs(eps_surprise):.1f}%")
    if matches:
        topics = ", ".join(m.topic for m in matches[:3])
        parts.append(f"confirms {len(matches)} standing concern{'s' if len(matches) != 1 else ''} ({topics})")

    lead = f"{event.ticker}: " + " and ".join(parts) if parts else f"{event.ticker}: event registered"
    tone = {"positive": "reads positive", "negative": "reads negative", "neutral": "reads mixed"}[direction]
    return f"{lead}. This {tone} against what Loom was already watching for."
