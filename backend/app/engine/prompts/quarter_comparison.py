"""Prompt and schema for explaining what changed in a quarter.

Separate from `risk_diff` because the two comparisons ask genuinely different
questions, and reusing the risk prompt here produced incoherent output: it
opens with "you are comparing a company's annual risk factors" and asks whether
each paragraph is a substantive *new risk*, which is the wrong question to put
to a gross-margin table.

What `diffing.py` hands this prompt is the set of paragraphs from Item 2
(management's discussion) that have no close match in the previous quarter's
filing. Much of that is numeric: segment tables, margin summaries, expense
lines. That is a feature rather than noise, because in a quarterly filing the
numbers *are* the narrative, and the useful judgement is which movements matter
and why. Restating a number is not analysis; saying what it implies is.
"""

from pydantic import BaseModel, Field

from app.engine.prompts.market_reaction import MARKET_REACTION_RULES, MarketReaction
from app.engine.prompts.plain_language import PLAIN_LANGUAGE_RULES


class QuarterChange(BaseModel):
    quote: str = Field(
        description="The verbatim paragraph or table row being assessed, copied exactly as given."
    )
    is_substantive: bool = Field(
        description="True if this reflects a real change in how the business is performing. "
        "False for routine restatement, boilerplate, or a number that moved trivially."
    )
    label: str = Field(
        description="Short plain-language name for the change, e.g. 'Gross margin jumped' "
        "or 'Cloud revenue growth slowed'."
    )
    what_changed: str = Field(
        description="The movement itself, with the figures. 'Gross margin rose from 60.5% to "
        "74.9% year on year.' State the direction and the size."
    )
    why_it_matters: str = Field(
        description="One sentence on what this means for the business, in ordinary words."
    )
    confidence: float = Field(description="Confidence that this is a real, material change, 0.0 to 1.0.")
    market_reaction: MarketReaction | None = Field(
        default=None, description="Required when is_substantive is true; omit otherwise."
    )


class QuarterComparisonResult(BaseModel):
    changes: list[QuarterChange] = Field(
        description="One entry per paragraph provided, in the same order."
    )


SYSTEM = f"""You are an equity research analyst comparing a company's latest \
quarterly report against the previous one, section by section.

You will be given passages from management's discussion of results that a text \
comparison found had no close match in the prior quarter's filing. Many will be \
financial tables. Those matter: in a quarterly report the numbers are the story.

Rules:

1. Mark `is_substantive` true when the passage shows the business performing \
differently: a margin that moved meaningfully, growth that accelerated or \
stalled, a cost line that jumped, a segment that turned. Mark it false for \
routine restatement, unchanged boilerplate, immaterial rounding, or a figure \
that moved a fraction of a percent.
2. When the passage is a table, read the movement out of it. "Gross margin rose \
from 60.5% to 74.9%" is the finding; reprinting the table is not.
3. Always give the direction and the size. A change without a magnitude is not \
useful to anyone deciding anything.
4. Copy `quote` verbatim from the passage you were given. Never edit it.
5. Be conservative. Quarterly filings restate a great deal every period, and a \
feed full of non-events trains the reader to ignore it.
6. Fill in `market_reaction` only when `is_substantive` is true.
7. Do not speculate about a specific share price and do not make buy/sell \
recommendations.

{MARKET_REACTION_RULES}

{PLAIN_LANGUAGE_RULES}"""


def build_user_content(
    ticker: str, current_period: str, prior_period: str, paragraphs: list[str]
) -> str:
    numbered = "\n\n".join(f"[{i + 1}] {para}" for i, para in enumerate(paragraphs))
    return (
        f"Company: {ticker}\n"
        f"Current quarterly report: {current_period}\n"
        f"Previous quarterly report: {prior_period}\n\n"
        f"These passages from management's discussion appear in the current "
        f"filing but did not match anything in the previous one. For each, say "
        f"what changed and whether it matters.\n\n{numbered}"
    )
