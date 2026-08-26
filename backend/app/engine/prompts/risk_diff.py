"""Prompt and schema for explaining genuinely changed risk-factor paragraphs.

This prompt never sees a whole filing. `diffing.py` has already established
*which* paragraphs changed by deterministic comparison; the only thing left
that needs language judgement is whether a change is substantive and why it
matters. That division is what keeps the diff cheap: a few thousand tokens
of changed text instead of two ~98k-token filings.
"""

from pydantic import BaseModel, Field

from app.engine.prompts.market_reaction import MARKET_REACTION_RULES, MarketReaction
from app.engine.prompts.plain_language import PLAIN_LANGUAGE_RULES


class RiskAssessment(BaseModel):
    quote: str = Field(description="The verbatim paragraph being assessed, copied exactly as given.")
    is_substantive: bool = Field(
        description="True if this is a genuinely new or materially changed risk; "
        "False if it is a rewording, reordering, or boilerplate refresh."
    )
    label: str = Field(description="Short name for the risk, e.g. 'customer concentration'.")
    why_it_matters: str = Field(description="One sentence on the practical consequence for an investor.")
    confidence: float = Field(description="Confidence that this is substantive, 0.0 to 1.0.")
    market_reaction: MarketReaction | None = Field(
        default=None, description="Required when is_substantive is true; omit otherwise."
    )


class RiskDiffResult(BaseModel):
    assessments: list[RiskAssessment] = Field(
        description="One entry per paragraph provided, in the same order."
    )


SYSTEM = f"""You are an equity research analyst comparing a company's latest \
annual risk factors against the prior year's.

You will be given paragraphs that a text comparison flagged as not matching \
anything in the prior filing. Most companies rewrite boilerplate every year, so \
many of these will be cosmetic. Your job is to separate the two.

Rules:

1. Mark `is_substantive` true only when the risk itself is new or materially \
broader, a newly named dependency, a new jurisdiction, a new category of \
threat, a materially escalated description. Mark it false for rewording, \
reordering, tightened legal phrasing, or updated dates and figures alone.
2. Copy `quote` verbatim from the paragraph you were given. Never edit it.
3. Be conservative. A false "new risk" is worse than a missed one, because it \
trains the reader to ignore the feed.
4. Fill in `market_reaction` only when `is_substantive` is true, leave it null \
otherwise, there is no market reaction to characterize for a cosmetic reword.
5. Do not speculate about a specific share price and do not make buy/sell \
recommendations.

{MARKET_REACTION_RULES}

{PLAIN_LANGUAGE_RULES}"""


def build_user_content(ticker: str, current_year: str, prior_year: str, paragraphs: list[str]) -> str:
    numbered = "\n\n".join(f"[{i + 1}] {para}" for i, para in enumerate(paragraphs))
    return (
        f"Company: {ticker}\n"
        f"Current filing: {current_year}\n"
        f"Prior filing: {prior_year}\n\n"
        f"These risk-factor paragraphs appear in the current filing but did not "
        f"match anything in the prior one. Assess each.\n\n{numbered}"
    )
