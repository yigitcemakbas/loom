"""Shared schema for the directional market-reaction characterization
attached to every risk, quote, and guidance-change finding.

This is deliberately three qualitative fields plus a rationale sentence,
never a number. Nothing in this pipeline has a pricing model, options data,
or technicals, so a specific percentage or price target would be fabricated
precision presented with false authority. What the model CAN do honestly is
characterize how markets typically read this TYPE of disclosure, grounded in
the finding itself, the same judgment a research note makes when it says
"we would expect shares to react negatively to this" without pretending to
know by how much or exactly when.
"""

from typing import Literal

from pydantic import BaseModel, Field

Direction = Literal["positive", "negative", "neutral"]
Magnitude = Literal["minor", "moderate", "major"]
Horizon = Literal["near_term", "multi_quarter", "structural"]


class MarketReaction(BaseModel):
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


MARKET_REACTION_RULES = """When you characterize likely market reaction (market_reaction fields):

- Judge DIRECTION (positive/negative/neutral) and MAGNITUDE (minor/moderate/major) \
by how markets typically react to this TYPE of disclosure, grounded in the specific \
finding, not by guessing at this particular stock's future price.
- NEVER state a specific percentage move, price target, or dollar figure. That would \
be fabricated precision. Qualitative direction and magnitude only.
- NEVER phrase this as a personalized recommendation ("you should buy", "sell now"). \
This characterizes the disclosure, not advice to any individual.
- If a finding is unlikely to move markets meaningfully, say so honestly \
(direction="neutral", magnitude="minor") rather than inventing significance to fill \
the field."""
