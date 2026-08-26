"""Prompt and schema for synthesising several recent findings into one read.

This prompt never sees a document. `clustering.py` has already established,
deterministically, that a company produced multiple disclosures inside a short
window and that those findings clear a materiality gate. What is left is the
one thing code cannot do: decide whether separate events are actually one
story, and say what that story is.

The model is handed findings that already carry verbatim, verified quotes, and
is asked to pick one of them as the anchor. It is never asked to produce new
quoted text, so there is no way for a paraphrase to enter the evidence chain;
`clustering.verify_anchor_quote` then confirms the returned anchor really is
one of the strings supplied.
"""

from pydantic import BaseModel, Field

from app.engine.prompts.market_reaction import MARKET_REACTION_RULES, MarketReaction
from app.engine.prompts.plain_language import PLAIN_LANGUAGE_RULES


class EmergingPatternResult(BaseModel):
    is_coherent: bool = Field(
        description="True only if these findings genuinely form one connected story. "
        "False when they are unrelated events that merely happened close together."
    )
    headline: str = Field(
        description="One line naming the pattern, e.g. 'Margin pressure now confirmed "
        "across three separate disclosures in eight days.'"
    )
    narrative: str = Field(
        description="Two or three sentences on what the combination means that no single "
        "finding showed on its own. Say what changed, not what each document said."
    )
    anchor_quote: str | None = Field(
        default=None,
        description="The single supplied quote that best anchors this pattern, copied "
        "EXACTLY as given. Never write a new quote. Null if none fits.",
    )
    confidence: float = Field(description="Confidence that this is a real pattern, 0.0 to 1.0.")
    market_reaction: MarketReaction | None = Field(
        default=None, description="Required when is_coherent is true; omit otherwise."
    )


SYSTEM = f"""You are an equity research analyst. You are given several findings \
that a company's disclosures produced inside a short window of days, and one \
question: do these add up to something that none of them shows alone?

Rules:

1. Be strict about `is_coherent`. Companies file many unrelated things in the \
same week. A pattern means the findings reinforce, escalate, or contradict one \
another, a supply problem confirmed twice from different angles, a risk \
disclosed and then quantified, an upbeat release followed by a hedged filing. \
Unrelated events happening close together are a coincidence, not a pattern, and \
you should say so by returning is_coherent false.
2. The value you add is synthesis, not summary. Do not restate the findings one \
by one, the reader already has them. Say what the combination establishes.
3. `anchor_quote` must be copied character for character from one of the quotes \
you were given. Never compose, edit, trim, or merge quotes. Return null rather \
than an approximation.
4. Weight timing. Findings days apart carry more force than the same findings \
months apart, that compression is the reason this pattern is worth flagging.
5. Fill in `market_reaction` only when `is_coherent` is true, leave it null \
otherwise.
6. Do not speculate about a specific share price and do not make buy/sell \
recommendations.

{MARKET_REACTION_RULES}

{PLAIN_LANGUAGE_RULES}"""


def build_user_content(
    ticker: str,
    window_days: int,
    findings: list[dict],
) -> str:
    """`findings` entries carry occurred_at, doc_subtype, summary, and quote."""
    blocks = []
    for i, finding in enumerate(findings, start=1):
        lines = [
            f"[{i}] {finding['occurred_at']} ({finding['doc_subtype']})",
            f"    Finding: {finding['summary']}",
        ]
        if finding.get("quote"):
            lines.append(f"    Quote: {finding['quote']}")
        if finding.get("market_direction"):
            lines.append(f"    Assessed reaction: {finding['market_direction']}")
        blocks.append("\n".join(lines))

    return (
        f"Company: {ticker}\n"
        f"Window: {window_days} days\n\n"
        f"These findings all came from disclosures inside that window. Decide "
        f"whether they form one connected story.\n\n" + "\n\n".join(blocks)
    )
