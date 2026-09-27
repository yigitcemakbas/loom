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
    # Written for a reader who already knows what accruals are.
    why_it_matters: str
    # The same thing said to someone who does not.
    #
    # Added because it was tested and failed. In the paired agent trial the
    # contradictions were the most-cited thing Loom produced, and one type,
    # `expansion_vs_returns`, defeated a reader outright on two companies: its
    # explanation turns on the idea that a ratio's numerator and denominator
    # are measured over different periods, which is true, is the whole point,
    # and is not a sentence anybody learns by reading it once.
    #
    # The rule for this field is that it may not contain a term of art. No
    # accruals, no asset base, no re-rating, no multiples. It says what
    # happened, what the other thing is, and why a person should care, and it
    # is allowed to be longer than the expert version to get there.
    plain: str = ""
    # The observation that would resolve the disagreement. Previously the
    # pessimistic side was passed into this slot by the case file, which put
    # "the cash conversion is among the worst of its peers" under the heading
    # "What would settle it", where it answers a different question than the one
    # the reader asked.
    settled_by: str = ""
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
    move=None,
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
            plain=(
                "The company's own language about recent results is upbeat. At the same "
                "time, less of the profit it reported actually turned up as cash than at "
                "almost any comparable company. Reported profit involves judgement calls "
                "about when a sale counts and what a cost is worth; cash in the bank does "
                "not. When the confident version and the cash version of the same quarter "
                "disagree, the cash version is the one that cannot be written persuasively."
            ),
            settled_by=(
                "The next quarterly cash flow statement. If the profit was real, the cash "
                "follows it within a quarter or two."
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
            plain=(
                "Management is talking the business up. Over the same period, the "
                "company's own executives and directors have been selling their shares. "
                "People sell for ordinary reasons all the time, a house or a tax bill, so "
                "one sale means nothing at all. What is worth a second look is that the "
                "people writing the optimistic language and the people selling into it "
                "are the same people."
            ),
            settled_by=(
                "The next set of insider filings. Buying, or the selling stopping, would "
                "resolve it; more selling while the language stays upbeat would not."
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
            plain=(
                "Sales are growing faster than at almost every comparable company. But a "
                "large share of the profit behind those sales has not arrived as cash. A "
                "company deciding when a sale counts as made has the most room to be "
                "optimistic precisely when it is growing fast, because there is so much "
                "genuine activity to account for. None of this says the company did "
                "anything wrong. It says the growth is worth confirming in cash before "
                "relying on it."
            ),
            settled_by=(
                "Whether cash collected catches up with sales reported over the next two "
                "or three quarters."
            ),
            factor_keys=["revenue_growth", "accruals"],
        ))

    # --- the balance sheet against the returns ------------------------
    expansion = percentile("asset_growth")
    returns = percentile("return_on_assets")
    if expansion is not None and expansion <= WEAK_PERCENTILE and returns is not None and returns >= STRONG_PERCENTILE:
        found.append(Contradiction(
            key="expansion_vs_returns",
            headline="It is earning well on what it owns, and it is buying a great deal more.",
            says_better="Profit per dollar of assets is among the best of comparable companies.",
            says_worse="Total assets grew faster than almost every comparable company.",
            why_it_matters=(
                "Today's return is earned on yesterday's smaller asset base. A company "
                "expanding this fast has to earn the same rate on the new assets to "
                "hold the ratio, and the companies that expand fastest have "
                "historically failed to. The two numbers are measuring different years."
            ),
            plain=(
                "Two facts that are each good news and together are a question. The "
                "company makes an unusually large profit relative to the size of the "
                "business, and the business has just grown much larger, faster than "
                "almost any comparable company. The catch is timing: the profit was "
                "earned by the smaller company that existed before the expansion, and it "
                "is being compared against a business that has only just got big. For "
                "that flattering number to hold, everything just bought has to earn as "
                "well as everything already owned, which is the hardest thing for a "
                "fast-growing company to do and the thing the fastest growers have "
                "historically failed at."
            ),
            settled_by=(
                "The same profit-per-dollar figure one or two years from now, once the "
                "new assets have had time to earn. If it holds up, the expansion worked."
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
                plain=(
                    "The share has risen more over the past year than almost every "
                    "comparable company. It now also costs more, relative to what the "
                    "company actually earns and owns, than almost every comparable "
                    "company. Those two facts have historically pointed opposite ways: a "
                    "share that has been rising has tended to keep rising for about a "
                    "year, and a share that is expensive has tended to disappoint over "
                    "several. So this is not really a disagreement about the company. It "
                    "is a question about how long you intend to hold it."
                ),
                settled_by=(
                    "Nothing in the next filing. This one is resolved by your own holding "
                    "period, not by the company."
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
                plain=(
                    "What this company says about itself reads well. What it reported "
                    "ranks among the weakest of its peers. These are two independent "
                    "halves of Loom: one reads the words in filings and calls, the other "
                    "does arithmetic on the filed numbers. When they disagree, one of "
                    "them is wrong, and the numbers are the half nobody can make sound "
                    "better than it is."
                ),
                settled_by="The next set of filed accounts, against the same peer group.",
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
                plain=(
                    "The reported numbers still rank among the best of comparable "
                    "companies, but what the company has started saying has turned "
                    "cautious. That order is the usual one: a company describes a problem "
                    "in words before it shows up in the accounts, because the words are "
                    "written about the quarter that is happening and the accounts are "
                    "about the one that finished. The gap between the two is where next "
                    "quarter's surprise usually comes from."
                ),
                settled_by="The next quarterly results, which is when the words become numbers.",
                factor_keys=["composite"],
            ))

    # --- what Loom read against what the market paid ------------------
    #
    # The third independent source. One reads language, one does arithmetic on
    # filed statements, and this one is a crowd of people with money at stake
    # reading the same document Loom read. When the first and the third reach
    # opposite conclusions about the same filing, that is the most informative
    # thing on the page, and it is a question rather than an answer: the market
    # is not always right, it is merely expensive to disagree with.
    #
    # Only a move *against* the reading counts. A filing the market ignored is
    # not a disagreement, it is an absence, and treating silence as a rebuttal
    # would mean Loom could only ever agree with the price.
    if stance in DIRECTIONAL_STANCES and move is not None and move.is_material:
        reading_positive = stance.endswith("positive")
        market_positive = move.abnormal_percent > 0
        if reading_positive != market_positive:
            rose = "rose" if market_positive else "fell"
            read_as = "encouraging" if reading_positive else "concerning"
            found.append(Contradiction(
                key="reading_vs_tape",
                headline=(
                    f"Loom read the filings as {read_as}. The market did the opposite."
                ),
                says_better=(
                    f"The shares {rose} {abs(move.abnormal_percent):.1f}% against the market "
                    f"in the fortnight after the most recent filing."
                    if market_positive else
                    "The filings and transcripts Loom analysed point positive."
                ),
                says_worse=(
                    "The filings and transcripts Loom analysed point negative."
                    if market_positive else
                    f"The shares {rose} {abs(move.abnormal_percent):.1f}% against the market "
                    f"in the fortnight after the most recent filing."
                ),
                why_it_matters=(
                    "Loom's reading and the tape are independent assessments of the same "
                    "disclosure, and a move of this size is not drift. Either the market "
                    "is weighing something the extraction did not reach, or it has not "
                    "finished reading. Both are worth establishing before acting on "
                    "either."
                ),
                plain=(
                    f"Loom read this company's recent filings as {read_as}. In the "
                    f"fortnight after the most recent one, the shares {rose} "
                    f"{abs(move.abnormal_percent):.1f}% more than the market did, which is "
                    f"a bigger move than this company usually makes. So two readers of the "
                    f"same document came to opposite conclusions, and one of them is Loom. "
                    f"That does not mean Loom is wrong. It means there is something in "
                    f"this filing that one of the two has weighed and the other has not, "
                    f"and finding out which is the useful next step."
                ),
                settled_by=(
                    "Reading the filing itself, which is linked from every finding below. "
                    "The disagreement is about its contents, not about anything Loom holds "
                    "separately."
                ),
                factor_keys=[],
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
