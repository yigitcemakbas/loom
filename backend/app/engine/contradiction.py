"""Where Loom's sources disagree about the same company.

Every other aggregation in this engine is a mean. The brief averages finding
directions, the composite averages factor percentiles, and both reduce a
company to a scalar. A mean is the wrong instrument for the most valuable thing
this database contains, because the moment worth acting on is precisely the one
where the evidence does NOT agree: management sounding confident while the cash
conversion deteriorates is not a neutral reading, it is a thesis, and averaging
it produces something close to zero.

So this module deliberately does not combine. It looks for pairs of signals
that point opposite ways, and reports the disagreement as the finding.

Three rules keep it from manufacturing drama.

**Both sides must be independently sourced.** A filing's optimistic tone
contradicting a risk factor from the same filing is not a contradiction, it is
how a 10-K is written. The pairs below cross source types: words against
numbers, insiders against management, the market's price against the filed
accounts.

**Both sides must be strong.** A mild positive against a mild negative is
ordinary noise in any company. Each side has a threshold, and both must clear
it, because a rule that fires on weak evidence fires constantly and teaches a
reader to ignore it.

**Nothing here claims a direction.** A contradiction is not bearish or bullish;
it is a statement that Loom's own evidence is inconsistent and that the reader
should look. Assigning it a direction would be inventing the synthesis this
module exists to avoid.
"""

from dataclasses import dataclass, field
from typing import Optional

from app.models.signal import Signal, SignalType

# A management-tone reading must be at least this far from neutral to be one
# side of a contradiction. Below it, tone is the ordinary register of a
# corporate document rather than a claim.
STRONG_TONE = 0.3

# Factor percentiles count as evidence only in the tails. The middle of a
# distribution is not saying anything for one side to disagree with.
WEAK_PERCENTILE = 0.2
STRONG_PERCENTILE = 0.8

# A brief stance must be at least a lean, not a shrug.
DIRECTIONAL_STANCES = {"positive", "strong_positive", "negative", "strong_negative"}


@dataclass(frozen=True)
class Contradiction:
    """Two pieces of evidence about one company that do not agree."""

    key: str
    # What the disagreement is, in one line a non-professional can read.
    headline: str
    # The optimistic side and the pessimistic side, each named with its source
    # so a reader can go and check rather than take this on faith.
    says_better: str
    says_worse: str
    # Why the disagreement is worth a reader's attention rather than being
    # noise. This is the part that makes it a thesis instead of an observation.
    why_it_matters: str
    signal_ids: list[str] = field(default_factory=list)
    factor_keys: list[str] = field(default_factory=list)


def _mean_tone(signals: list[Signal]) -> Optional[float]:
    """Average management tone across the sentiment findings.

    Only SENTIMENT_SHIFT carries a tone score. Reading tone off the other
    finding types would double-count the same documents through a second
    channel and make every company look self-contradictory.
    """
    scores = [
        s.sentiment_score for s in signals
        if s.signal_type == SignalType.SENTIMENT_SHIFT and s.sentiment_score is not None
    ]
    if not scores:
        return None
    return sum(scores) / len(scores)


def _insider_direction(signals: list[Signal]) -> Optional[tuple[str, Signal]]:
    """Which way insiders were trading, from the most recent insider finding."""
    insider = [s for s in signals if s.signal_type == SignalType.INSIDER_ACTIVITY]
    if not insider:
        return None
    latest = max(insider, key=lambda s: s.occurred_at)
    if latest.market_direction not in ("positive", "negative"):
        return None
    return latest.market_direction, latest


def find_contradictions(
    signals: list[Signal],
    factors: dict[str, float],
    *,
    stance: Optional[str] = None,
) -> list[Contradiction]:
    """Every disagreement Loom can see between its own sources.

    `factors` maps a factor key to its percentile, already oriented so that
    high is good. Absent keys are absent rather than defaulted: a factor that
    could not be computed has said nothing, and treating silence as a middling
    reading would suppress real contradictions and invent fake ones in equal
    measure.
    """
    found: list[Contradiction] = []
    tone = _mean_tone(signals)
    insiders = _insider_direction(signals)

    def percentile(key: str) -> Optional[float]:
        return factors.get(key)

    # --- words against the cash ---------------------------------------
    quality = percentile("accruals")
    if tone is not None and tone >= STRONG_TONE and quality is not None and quality <= WEAK_PERCENTILE:
        found.append(Contradiction(
            key="tone_vs_cash",
            headline="Management sounds confident, the cash does not agree.",
            says_better=f"Management tone across recent disclosures reads positive ({tone:+.2f}).",
            says_worse=(
                "The share of profit that did not arrive as cash is among the worst "
                "of comparable companies."
            ),
            why_it_matters=(
                "Accruals are where accounting judgement lives, and judgement bends "
                "toward the answer management wants. Confident language over widening "
                "accruals is the specific pattern that precedes a restatement or a "
                "disappointing quarter. The thing that settles it is next quarter's "
                "cash conversion."
            ),
            signal_ids=[str(s.id) for s in signals if s.signal_type == SignalType.SENTIMENT_SHIFT],
            factor_keys=["accruals"],
        ))

    # --- words against the people who know ----------------------------
    if tone is not None and tone >= STRONG_TONE and insiders and insiders[0] == "negative":
        found.append(Contradiction(
            key="tone_vs_insiders",
            headline="Management sounds confident, insiders are selling.",
            says_better=f"Management tone across recent disclosures reads positive ({tone:+.2f}).",
            says_worse="Loom recorded a cluster of insider selling.",
            why_it_matters=(
                "Insiders sell for many innocent reasons, which is why a single sale "
                "means nothing. What makes this worth reading is the disagreement: the "
                "people writing the optimistic language and the people trading the "
                "stock are the same people."
            ),
            signal_ids=[str(insiders[1].id)],
        ))

    # --- growth against the quality of it -----------------------------
    growth = percentile("revenue_growth")
    if growth is not None and growth >= STRONG_PERCENTILE and quality is not None and quality <= WEAK_PERCENTILE:
        found.append(Contradiction(
            key="growth_vs_quality",
            headline="Revenue is growing fast, the profit behind it is not cash.",
            says_better="Revenue growth is among the best of comparable companies.",
            says_worse=(
                "The share of profit that did not arrive as cash is among the worst."
            ),
            why_it_matters=(
                "Fast growth creates the room for aggressive revenue recognition, and "
                "this is what that looks like on the statements. It is not evidence of "
                "anything improper; it is the combination that deserves the next "
                "cash flow statement read carefully rather than skimmed."
            ),
            factor_keys=["revenue_growth", "accruals"],
        ))

    # --- the balance sheet against the returns ------------------------
    expansion = percentile("asset_growth")
    returns = percentile("return_on_assets")
    if expansion is not None and expansion <= WEAK_PERCENTILE and returns is not None and returns >= STRONG_PERCENTILE:
        found.append(Contradiction(
            key="expansion_vs_returns",
            headline="Returns are excellent, the balance sheet is expanding fast.",
            says_better="Profit per dollar of assets is among the best of comparable companies.",
            says_worse="Total assets grew faster than almost every comparable company.",
            why_it_matters=(
                "Today's return is earned on yesterday's smaller asset base. A company "
                "expanding this fast has to earn the same rate on the new assets to "
                "hold the ratio, and the companies that expand fastest have "
                "historically failed to. The two numbers are measuring different years."
            ),
            factor_keys=["asset_growth", "return_on_assets"],
        ))

    # --- the market against the filings -------------------------------
    momentum = percentile("momentum")
    if momentum is not None and momentum >= STRONG_PERCENTILE:
        weak_value = [
            key for key in ("earnings_yield", "sales_yield", "book_to_price")
            if (p := percentile(key)) is not None and p <= WEAK_PERCENTILE
        ]
        if len(weak_value) >= 2:
            found.append(Contradiction(
                key="price_vs_value",
                headline="The share has run hard and is now expensive on the numbers.",
                says_better="Price performance over the past year is among the best of comparable companies.",
                says_worse=(
                    "On "
                    f"{len(weak_value)} separate valuation measures the company is among "
                    "the most expensive of its peers."
                ),
                why_it_matters=(
                    "Both can be true and both have historically predicted, in opposite "
                    "directions: momentum has tended to continue over a year and expensive "
                    "companies have tended to disappoint over several. The disagreement is "
                    "really about how long you intend to hold."
                ),
                factor_keys=["momentum", *weak_value],
            ))

    # --- what Loom read against what the company reported -------------
    composite = percentile("composite")
    if stance in DIRECTIONAL_STANCES and composite is not None:
        reading_positive = stance.endswith("positive")
        if reading_positive and composite <= WEAK_PERCENTILE:
            found.append(Contradiction(
                key="reading_vs_numbers",
                headline="What Loom read is positive, what the company reported is weak.",
                says_better="The filings and transcripts Loom analysed point positive.",
                says_worse="The reported financials rank among the weakest of comparable companies.",
                why_it_matters=(
                    "These two halves of Loom are independent: one reads language, the "
                    "other does arithmetic on filed statements. When they disagree, at "
                    "least one is wrong, and the arithmetic is the half that cannot be "
                    "written persuasively."
                ),
                factor_keys=["composite"],
            ))
        elif not reading_positive and composite >= STRONG_PERCENTILE:
            found.append(Contradiction(
                key="reading_vs_numbers",
                headline="What Loom read is negative, what the company reported is strong.",
                says_better="The reported financials rank among the best of comparable companies.",
                says_worse="The filings and transcripts Loom analysed point negative.",
                why_it_matters=(
                    "Language leads the statements. A company whose numbers still look "
                    "strong while its disclosures turn cautious is describing something "
                    "that has not reached the accounts yet, and that gap is usually "
                    "where the next quarter's surprise comes from."
                ),
                factor_keys=["composite"],
            ))

    return found


__all__ = [
    "DIRECTIONAL_STANCES",
    "STRONG_PERCENTILE",
    "STRONG_TONE",
    "WEAK_PERCENTILE",
    "Contradiction",
    "find_contradictions",
]
