"""Prompt and response schema for the per-document analysis call.

One call extracts sentiment, notable quotes, and key risks together. Three
separate calls would send the same filing text three times and triple the
input cost for no gain, since all three judgements read the same material.

The schema demands a verbatim `quote` on every finding. That is not decoration:
the dashboard shows the quote as the receipt for each signal, and the
verification step greps these strings back against the stored filing. A
paraphrase would break both.

Every finding also carries a market_reaction (see market_reaction.py):
qualitative direction/magnitude/horizon, never a fabricated price number.
"""

from pydantic import BaseModel, Field

from app.engine.prompts.market_reaction import MARKET_REACTION_RULES, MarketReaction
from app.engine.prompts.plain_language import PLAIN_LANGUAGE_RULES


class ExtractedQuote(BaseModel):
    quote: str = Field(description="Verbatim sentence or passage copied exactly from the filing.")
    why_it_matters: str = Field(description="One sentence on why an investor should care.")
    market_reaction: MarketReaction


class ExtractedRisk(BaseModel):
    label: str = Field(description="Short name for the risk, e.g. 'supply chain concentration'.")
    quote: str = Field(description="Verbatim sentence from the filing that states this risk.")
    why_it_matters: str = Field(description="One sentence on the practical consequence.")
    market_reaction: MarketReaction


class GuidanceChange(BaseModel):
    description: str = Field(description="What changed in forward guidance or outlook.")
    market_reaction: MarketReaction


class DocumentAnalysisResult(BaseModel):
    sentiment_score: float = Field(
        description="Management tone from -1.0 (very negative) to 1.0 (very positive)."
    )
    sentiment_summary: str = Field(description="One sentence characterising the tone and why.")
    confidence: float = Field(description="Confidence in this analysis, 0.0 to 1.0.")
    notable_quotes: list[ExtractedQuote] = Field(
        description="Up to 3 passages a careful investor would want to see. Empty list if none stand out."
    )
    key_risks: list[ExtractedRisk] = Field(
        description="Up to 5 substantive risks. Empty list if the document does not discuss risk."
    )
    guidance_change: GuidanceChange | None = Field(
        default=None,
        description="Any explicit change to forward guidance or outlook, else null.",
    )


SYSTEM = f"""You are an equity research analyst reading SEC filings for a \
professional investor. Your job is to surface what is decision-relevant and \
skip what is boilerplate.

Rules you must follow:

1. Every `quote` field must be copied VERBATIM from the document, character for \
character. Never paraphrase, summarise, correct, or reflow a quote. If you \
cannot find an exact supporting sentence, omit the item entirely.
2. Prefer the specific over the generic. "Supply is concentrated with a single \
Taiwanese foundry" is useful; "the company faces competition" is not. Filings \
are full of generic risk language that appears every year, that is not signal.
3. Judge tone from what management actually commits to, not from how upbeat the \
adjectives are. Hedged guidance in confident language is a negative signal.
4. Report low confidence when the document is thin, boilerplate-heavy, or \
truncated. An honest low number is more useful than false certainty.
5. Do not speculate about a specific share price and do not make buy/sell \
recommendations.

{MARKET_REACTION_RULES}

{PLAIN_LANGUAGE_RULES}"""


def build_user_content(ticker: str, doc_subtype: str, filed: str, text: str) -> str:
    return (
        f"Company: {ticker}\n"
        f"Filing type: {doc_subtype}\n"
        f"Filed: {filed}\n\n"
        f"Analyse the following filing excerpt.\n\n"
        f"---\n{text}\n---"
    )
