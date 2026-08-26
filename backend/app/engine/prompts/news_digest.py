"""Prompt and schema for reading a ticker's recent news in one pass.

Filings get one analysis call each because each one is a substantial document.
News is the opposite: items run a couple of hundred characters, and analysing
them individually spent one call per blurb, which measured at roughly two
thirds of the entire engine's call budget for a ticker while producing the
least per-call value of anything in the pipeline.

Reading them together is also simply the better analysis. A single item saying
a supplier raised prices is barely a signal; five items across a week saying it
is one. The digest sees the run, an item-at-a-time reader cannot.

Findings carry `item_index` so every signal still points at the specific
document it came from, and `quote` is verified against that item's text before
the signal is stored, so batching costs nothing in traceability.
"""

from pydantic import BaseModel, Field

from app.engine.prompts.market_reaction import MARKET_REACTION_RULES, MarketReaction
from app.engine.prompts.plain_language import PLAIN_LANGUAGE_RULES


class NewsFinding(BaseModel):
    item_index: int = Field(
        description="1-based index of the item this finding came from, exactly as numbered in the input."
    )
    summary: str = Field(description="One sentence on what this means for the company.")
    quote: str = Field(
        description="A verbatim sentence or headline copied exactly from that item. Never paraphrase."
    )
    market_reaction: MarketReaction


class NewsDigestResult(BaseModel):
    sentiment_score: float = Field(
        description="Overall tone of this news run, -1.0 (very negative) to 1.0 (very positive)."
    )
    sentiment_summary: str = Field(
        description="One sentence characterising what the news flow as a whole says about the company."
    )
    confidence: float = Field(description="Confidence in this read, 0.0 to 1.0.")
    findings: list[NewsFinding] = Field(
        description="Only items a professional investor would act on. Usually far fewer than "
        "the number supplied. Empty list when the run is all noise."
    )


SYSTEM = f"""You are an equity research analyst reading a week's news coverage \
of one company in a single pass.

Most financial news is noise: recycled market commentary, analyst chatter, and \
listicles. Your job is to find the few items that carry actual information, and \
to say what the run of coverage adds up to.

Rules:

1. Be severe about what counts as a finding. A typical batch yields zero to \
three. Returning a finding for every item you were given means you have not \
filtered anything, which is worse than useless because it buries the real ones.
2. Prefer items reporting something that happened, a contract, a ruling, a \
price change, a departure, over items reporting what someone thinks about the \
stock. Opinion pieces and price-target changes are not company events.
3. `quote` must be copied VERBATIM from the item it came from, character for \
character. If no sentence in the item supports the finding, omit the finding.
4. `item_index` must be the number shown against that item. Findings whose \
index does not match a supplied item are discarded.
5. Judge `sentiment_score` on the run as a whole, not on the loudest headline. \
Several mild negatives are a real negative; one dramatic headline usually is not.
6. Do not speculate about a specific share price and do not make buy/sell \
recommendations.

{MARKET_REACTION_RULES}

{PLAIN_LANGUAGE_RULES}"""


def build_user_content(ticker: str, window_days: int, items: list[dict]) -> str:
    """`items` entries carry published (str), title, and text."""
    blocks = []
    for index, item in enumerate(items, start=1):
        blocks.append(
            f"[{index}] {item['published']} | {item['title']}\n{item['text']}"
        )
    return (
        f"Company: {ticker}\n"
        f"Window: the last {window_days} days, {len(items)} items\n\n"
        f"Read these together and report only what matters.\n\n" + "\n\n".join(blocks)
    )
