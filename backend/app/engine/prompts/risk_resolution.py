"""Prompt and schema for explaining risk-factor paragraphs a filer dropped.

The mirror of risk_diff.py, and it exists because for most of this engine's life
the comparison ran one way only. `diffing.py` iterated the current filing looking
for paragraphs absent from the prior one, so Loom could see a risk appear and
never see one resolve, and every finding the diff could produce was negative.

Judging a removal is harder than judging an addition, and the prompt is written
around that asymmetry. A new paragraph is at least evidence that the company
chose to say something. A missing paragraph might mean the risk is gone, or that
two paragraphs were merged, or that the section was reorganised, or that counsel
simply tightened it. Only the first is news, so the bar here is deliberately
higher than for additions: a company is not credited with resolving a risk
because its lawyers consolidated a paragraph.

Like risk_diff.py, this never sees a whole filing. The deterministic comparison
has already established that the paragraph has no close match in the current
document; the only thing left needing language judgement is whether its absence
means anything.
"""

from pydantic import BaseModel, Field

from app.engine.prompts.market_reaction import MARKET_REACTION_RULES, MarketReaction
from app.engine.prompts.plain_language import PLAIN_LANGUAGE_RULES


class ResolutionAssessment(BaseModel):
    quote: str = Field(
        description="The verbatim paragraph from the prior filing, copied exactly as given."
    )
    is_resolved: bool = Field(
        description="True only if the company appears to have genuinely stopped "
        "carrying this risk. False if the text was merged into another "
        "paragraph, reorganised, reworded, or shortened by counsel."
    )
    label: str = Field(description="Short name for the risk, e.g. 'Ireland tax assessment'.")
    why_it_matters: str = Field(
        description="One sentence on what it means for an investor that this "
        "disclosure is no longer being made."
    )
    confidence: float = Field(description="Confidence that this is a genuine resolution, 0.0 to 1.0.")
    market_reaction: MarketReaction | None = Field(
        default=None, description="Required when is_resolved is true; omit otherwise."
    )


class RiskResolutionResult(BaseModel):
    assessments: list[ResolutionAssessment] = Field(
        description="One entry per paragraph provided, in the same order."
    )


SYSTEM = f"""You are an equity research analyst comparing a company's latest \
annual risk factors against the prior year's.

You will be given paragraphs that appeared in the PRIOR filing and that a text \
comparison could not find anywhere in the LATEST one. Your job is to decide, for \
each, whether the company has genuinely stopped disclosing that risk.

Be more sceptical here than you would be about a newly added paragraph. Filers \
reorganise risk sections constantly: they merge two risks into one, split one \
into two, move a paragraph to a different heading, or let counsel shorten it. \
None of those mean the risk went away, and all of them look identical to a \
resolution from the outside.

Rules:

1. Mark `is_resolved` true only when the risk itself appears to be gone, a \
resolved legal matter, a divested business, a concluded investigation, an \
expired contingency. Mark it false when the same underlying risk is plausibly \
still disclosed in different words, when the paragraph reads like boilerplate \
that was simply trimmed, or when you cannot tell.
2. Copy `quote` verbatim from the paragraph you were given. Never edit it.
3. Be conservative, and note that conservative here means false. A company that \
is wrongly credited with resolving a risk reads better than it is, which is the \
more dangerous error: a missed resolution costs the reader nothing, and a \
fictional one is reassurance the filings do not support.
4. Fill in `market_reaction` only when `is_resolved` is true, leave it null \
otherwise.
5. Do not speculate about a specific share price and do not make buy/sell \
recommendations.

{MARKET_REACTION_RULES}

{PLAIN_LANGUAGE_RULES}"""


def build_user_content(
    *, ticker: str, current_period: str, prior_period: str, paragraphs: list[str]
) -> str:
    numbered = "\n\n".join(
        f"[{i + 1}] {paragraph}" for i, paragraph in enumerate(paragraphs)
    )
    return (
        f"Company: {ticker}\n"
        f"Latest filing: {current_period}\n"
        f"Prior filing: {prior_period}\n\n"
        f"These paragraphs were in the prior filing's risk factors and have no "
        f"close match in the latest one. For each, decide whether the company "
        f"has genuinely stopped disclosing that risk.\n\n"
        f"{numbered}"
    )
