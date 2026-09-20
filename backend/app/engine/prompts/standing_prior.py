"""Prompt and schema for the standing prior: what to watch on a company, in advance.

Every other prompt in this project reacts to a document. This one is the only
one that runs with no event in front of it, and it asks a deliberately
different question: not "what does this say" but "what would matter if it
happened".

The output has one job that shapes the whole schema. It has to be matchable by
code, in microseconds, against an event that has not occurred yet. That is why
each watch item carries explicit `keywords` rather than only prose: prose is
for the reader, keywords are what `engine/reaction.py` scans an incoming
filing or headline for without calling a model. A beautifully written watch
item with no keywords is invisible to the fast path and therefore useless.
"""

from pydantic import BaseModel, Field

from app.engine.prompts.plain_language import PLAIN_LANGUAGE_RULES

PROMPT_VERSION = "2026-09-20.1"


class WatchItem(BaseModel):
    topic: str = Field(
        description="Short name for the thing to watch, e.g. 'gross margin compression'."
    )
    keywords: list[str] = Field(
        description=(
            "3 to 8 lowercase words or short phrases that would plausibly appear in a "
            "filing, press release, or headline about this topic. These are matched "
            "literally by code against incoming text, so prefer the words a company "
            "would actually use over abstract labels. Include obvious synonyms."
        )
    )
    watch_for: str = Field(
        description="The specific observable that would confirm this, in one sentence."
    )
    direction_if_confirmed: str = Field(
        description="'positive', 'negative', or 'neutral': how the stock would likely be affected."
    )
    severity: str = Field(
        description="'minor', 'moderate', or 'major': how much it would matter if confirmed."
    )
    why_it_matters: str = Field(description="One sentence on the practical consequence.")
    evidence_quote: str | None = Field(
        default=None,
        description="Verbatim sentence from the source material that motivates this item, if any.",
    )


class StandingPriorResult(BaseModel):
    summary: str = Field(
        description="One sentence a reader would want if they could only see one line about this company today."
    )
    watch_items: list[WatchItem] = Field(
        description="Up to 8 things worth watching. Ordered most important first. Empty list if the material does not support any."
    )
    guidance_notes: str | None = Field(
        default=None,
        description="What management has said to expect, in one sentence, or null if nothing explicit.",
    )
    already_priced: list[str] = Field(
        default_factory=list,
        description="Topics the market has visibly already reacted to, so they should not be treated as new.",
    )


SYSTEM = f"""You are preparing an analyst's watch list for one company, before any news arrives.

You are NOT summarising. You are anticipating. Given what this company has
disclosed recently and how its stock has behaved, list the specific things that
would move it if they happened next.

Rules:
- Every watch item must be concrete enough to recognise in text. "Execution
  risk" is useless. "Flash Ventures lease guarantees triggering a capital call"
  is usable.
- keywords are matched literally by software against incoming filings and
  headlines. Choose words the company itself would use.
- Do not predict prices, percentages, or dates. State what to watch, not what
  will happen.
- If the stock has already fallen heavily on a topic, put that topic in
  already_priced rather than treating it as a fresh risk.
- Prefer few sharp items over many vague ones. An empty list is better than a
  list of generic corporate risks that would apply to any company.

{PLAIN_LANGUAGE_RULES}
"""
