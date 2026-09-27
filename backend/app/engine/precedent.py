"""What has usually followed a disclosure like this one, at other companies.

A reader who meets "supply chain concentration: a single foundry manufactures
most of our components" has no way to calibrate it. It is written in the
register of a serious problem, it appears under a heading called Risk Factors,
and nothing on the page says whether disclosures of that kind have historically
been followed by anything at all. The genre correction in engine/disclosure.py
fixed this for the *engine*, which was scoring such sentences as evidence. It
did nothing for the *reader*, who is still doing the same arithmetic by eye.

So this module measures it. Findings are tagged with topics from a fixed list,
each topic's historical outcomes are collected from what the market actually
did afterwards, and a reader meeting a new disclosure is told what followed the
comparable ones.

**It is not a forecast, and the wording is load-bearing.** This reports a
distribution of things that have already happened and attaches the count and
the spread to every one of them. It never says what will happen, and the most
common thing it says is that the comparable cases point nowhere.

Five rules keep it from manufacturing a pattern.

**The topics are fixed in advance.** They are written from what filings
actually discuss, before any outcome was measured, and they are matched by
literal keywords. Mining the corpus for phrases that precede falls would find
some, and they would be noise with a name.

**One filing is one case.** An annual report yields forty findings which all
measure the same fortnight. Counting them as forty precedents turns a single
company's single bad quarter into overwhelming evidence.

**The company itself is excluded.** A precedent built partly from Apple's own
history, shown against an Apple finding, is Apple predicting Apple. What a
reader wants to know is what happened at *other* companies.

**The spread is reported, always.** A median of -1% across cases running from
-9% to +11% is not a tendency, and showing the median alone would present it as
one. Where the range straddles zero, that is what the reader is told.

**A direction is claimed only against a stated bar.** Measured on the stored
corpus, no topic clears it: clustered by filing, every one of the fourteen
topics produces a t-statistic between +0.18 and +1.28, against a bar of 2.9
once the fourteen tests are accounted for. So the honest output today is almost
always that the comparable cases say nothing, and that is a genuinely useful
thing to tell someone who has just read an alarming sentence.
"""

from dataclasses import dataclass, field
from statistics import median, pstdev
from typing import Iterable, Optional

# The topics, fixed in advance and matched by literal keywords.
#
# Written from what filings and calls actually discuss rather than from what
# correlates with anything, and deliberately before any outcome was measured.
# The alternative, mining the corpus for phrases that tend to precede falls,
# would certainly find some at this sample size, and they would be noise with a
# name attached. Same discipline as the factor library, which admits only
# published and replicated measures rather than whatever ranks well here.
TOPICS: dict[str, tuple[str, ...]] = {
    "supply_concentration": (
        "supplier", "supply chain", "foundry", "concentration", "single source",
        "sole source", "manufacturing partner",
    ),
    "regulation": (
        "regulat", "antitrust", "compliance", "legislat", "doj", "ftc",
        "european union", "government-set",
    ),
    "litigation": ("litigat", "lawsuit", "court", "settlement", "patent infring", "damages"),
    "tax": ("tax", "irs", "deficiency", "transfer pricing"),
    "demand": ("demand", "backlog", "bookings", "order volume", "end market"),
    "margin_cost": ("margin", "input cost", "pricing", "inflation", "cost of goods", "component cost"),
    "capital_spending": (
        "capital expenditure", "capex", "data center", "infrastructure investment",
        "build-out", "buildout", "fab ",
    ),
    "financing": ("debt", "senior notes", "offering", "issuance", "dilution", "leverage", "refinanc"),
    "leadership": ("chief executive", "resign", "stepping down", "succession", "board member"),
    "competition": ("competit", "open-source", "open source", "displac", "market share", "rival"),
    "cyber": ("cyber", "breach", "data security", "ransomware"),
    "labour": ("labor", "labour", "workforce", "headcount", "union", "layoff", "attrition"),
    "trade": ("tariff", "export control", "trade restriction", "sanction", "geopolit"),
    "guidance": ("guidance", "outlook", "forecast", "full-year expectation"),
}

# Plain names, because a topic key is a programming artefact and a reader
# should never meet one.
TOPIC_LABELS: dict[str, str] = {
    "supply_concentration": "depending on a small number of suppliers",
    "regulation": "regulatory and government pressure",
    "litigation": "litigation and legal claims",
    "tax": "tax disputes and assessments",
    "demand": "demand for what the company sells",
    "margin_cost": "costs and profit margins",
    "capital_spending": "heavy spending on plant and equipment",
    "financing": "borrowing and share issuance",
    "leadership": "changes at the top of the company",
    "competition": "competitive and technological pressure",
    "cyber": "cyber security and data breaches",
    "labour": "the workforce",
    "trade": "tariffs, export controls and geopolitics",
    "guidance": "the company's own forecasts",
}

# Independent cases, after one filing has been collapsed to one case, before a
# precedent is reported at all. Twelve is not many; it is where the count stops
# being an anecdote, and the spread is reported alongside it precisely because
# twelve cases cannot support a confident claim on their own.
MIN_CASES = 12

# And from at least this many distinct companies. Twelve cases drawn from three
# companies is three companies' experience repeated, and a precedent is
# supposed to be about what happens generally.
MIN_COMPANIES = 8

# A sector-specific precedent needs its own floor before it is preferred over
# the all-sector one. Below it, "what happened at technology companies" is a
# smaller and noisier version of "what happened", not a sharper one.
MIN_SECTOR_CASES = 20

# What it takes to claim a direction rather than report a spread.
#
# Three conditions together, and they are meant to be hard. The share condition
# says the cases mostly went one way; the t condition says the average is
# distinguishable from zero; and the bar is raised for having tested every
# topic, which is the correction this project has applied to every other
# measurement it has made and got wrong once by omitting.
DIRECTIONAL_SHARE = 0.70
DIRECTIONAL_T = 2.9

# Below this, a median move is reported as "around nothing" rather than as a
# number. Printing "-0.2%" invites a reader to treat two tenths of a percent,
# inside a twenty point spread, as a finding.
NEGLIGIBLE_PERCENT = 1.0


def topics_of(text: str) -> set[str]:
    """Which of the fixed topics a piece of text is about.

    A finding may carry several, and that is correct rather than a defect: a
    disclosure about tariffs raising component costs is genuinely about both
    trade and margins, and forcing a single label would discard half of what it
    said.
    """
    low = (text or "").lower()
    return {
        topic for topic, keywords in TOPICS.items()
        if any(keyword in low for keyword in keywords)
    }


def topics_of_signal(signal) -> set[str]:
    return topics_of(
        f"{getattr(signal, 'summary', '') or ''} {getattr(signal, 'detail', '') or ''}"
    )


def primary_topic(text: str) -> Optional[str]:
    """The single topic a piece of text is most about.

    A finding can legitimately carry several topics, and for *measuring* the
    precedent base that is right: a disclosure about tariffs raising component
    costs is evidence about both trade and margins, and dropping one would
    discard half of what it said.

    Showing a reader a precedent is the opposite problem. Only one can go on
    the row, and picking arbitrarily from a set does exactly what it sounds
    like: a finding headed "Trade disputes and international conflict" was
    calibrated against the supply-concentration record, because set iteration
    order decided it. So the winner is the topic whose vocabulary the text
    actually uses most, with ties broken by the fixed order the topics are
    declared in, which makes the choice reproducible rather than merely
    plausible.
    """
    low = (text or "").lower()
    best: Optional[str] = None
    best_hits = 0
    for topic, keywords in TOPICS.items():
        hits = sum(1 for keyword in keywords if keyword in low)
        if hits > best_hits:
            best, best_hits = topic, hits
    return best


def primary_topic_of_signal(signal) -> Optional[str]:
    return primary_topic(
        f"{getattr(signal, 'summary', '') or ''} {getattr(signal, 'detail', '') or ''}"
    )


@dataclass(frozen=True)
class Case:
    """One filing, at one company, and what the market did in the fortnight after.

    The unit is the filing rather than the finding. Forty findings from one
    annual report all measure the same fortnight, so counting them separately
    would turn a single company's single bad quarter into overwhelming
    evidence for whatever those forty sentences happened to be about.
    """

    topic: str
    sector: Optional[str]
    ticker: str
    move_percent: float


@dataclass(frozen=True)
class Precedent:
    """What followed comparable disclosures, with everything needed to doubt it."""

    topic: str
    # None when the precedent is drawn from every sector rather than one.
    sector: Optional[str]
    cases: int
    companies: int
    median_percent: float
    share_negative: float
    # The tenth and ninetieth percentiles of the outcomes. Reported always,
    # because a median without them presents a coin flip as a tendency.
    low_percent: float
    high_percent: float
    # None when the outcomes had no spread at all, which is not the same as
    # having no signal. Returning 0.0 there reads to every caller as "no
    # evidence whatsoever", so a set of cases that went the same way every
    # single time would be reported as the weakest possible result rather than
    # the strongest. The same mistake is recorded against `calculate_z_score`
    # in the statistics package, which is where this fix is copied from.
    t_statistic: Optional[float]

    @property
    def is_directional(self) -> bool:
        """Whether the cases actually went one way often enough to say so."""
        one_sided = max(self.share_negative, 1 - self.share_negative)
        if one_sided < DIRECTIONAL_SHARE:
            return False
        if self.t_statistic is None:
            # No dispersion and a non-zero middle: every comparable case went
            # the same way by the same amount. Real price moves never do this,
            # so in practice it is unreachable, but a branch that would report
            # perfect consistency as no evidence is wrong whether or not
            # anything reaches it.
            return abs(self.median_percent) >= NEGLIGIBLE_PERCENT
        return abs(self.t_statistic) >= DIRECTIONAL_T

    @property
    def summary(self) -> str:
        """The sentence a reader sees. Never a forecast.

        Deliberately says "Loom has read" rather than "history shows". The
        record behind this is one engine's reading of twenty-odd companies over
        about a year, and dressing that as history would be the single most
        misleading thing this module could do.
        """
        where = f" at other {self.sector.lower()} companies" if self.sector else " at other companies"
        label = TOPIC_LABELS.get(self.topic, self.topic.replace("_", " "))
        stem = (
            f"Of the {self.cases} comparable disclosures about {label}{where} "
            f"that Loom has read"
        )

        if self.is_directional:
            direction = "fell" if self.share_negative > 0.5 else "rose"
            share = max(self.share_negative, 1 - self.share_negative)
            return (
                f"{stem}, the shares {direction} afterwards in {round(share * 100)}% of "
                f"cases, by about {abs(self.median_percent):.1f}% at the midpoint."
            )

        middle = (
            "went nowhere in particular"
            if abs(self.median_percent) < NEGLIGIBLE_PERCENT
            else f"moved about {abs(self.median_percent):.1f}% "
                 f"{'down' if self.median_percent < 0 else 'up'} at the midpoint"
        )
        return (
            f"{stem}, the shares {middle} afterwards, with outcomes running from "
            f"{self.low_percent:.0f}% to +{self.high_percent:.0f}%. A disclosure like this "
            f"has not, by itself, told you which way the shares went."
        )


@dataclass
class PrecedentBase:
    """Every measured precedent, looked up by topic and sector."""

    by_topic: dict[str, list[Case]] = field(default_factory=dict)

    def lookup(
        self, topic: str, sector: Optional[str], *, exclude_ticker: Optional[str] = None
    ) -> Optional[Precedent]:
        """What followed comparable disclosures elsewhere.

        `exclude_ticker` is not optional in spirit. A precedent built partly
        from this company's own history, shown against this company's finding,
        is the company predicting itself, and at these sample sizes one firm
        with six filings can be half the evidence.
        """
        cases = [
            case for case in self.by_topic.get(topic, [])
            if exclude_ticker is None or case.ticker != exclude_ticker
        ]
        if not cases:
            return None

        # Sector first, where the sector has enough of its own to be sharper
        # rather than merely smaller.
        if sector is not None:
            same = [case for case in cases if case.sector == sector]
            found = _summarise(topic, sector, same)
            if found is not None and found.cases >= MIN_SECTOR_CASES:
                return found

        return _summarise(topic, None, cases)


def _summarise(topic: str, sector: Optional[str], cases: list[Case]) -> Optional[Precedent]:
    """Fold cases into a precedent, or refuse when there are too few.

    Refusing is the common outcome and the correct one. Reporting "of the 3
    comparable disclosures Loom has read" gives a number the shape of evidence
    without the substance, and a reader has no way to discount it by eye.
    """
    if len(cases) < MIN_CASES:
        return None
    companies = len({case.ticker for case in cases})
    if companies < MIN_COMPANIES:
        return None

    moves = sorted(case.move_percent for case in cases)
    n = len(moves)
    mean = sum(moves) / n
    spread = pstdev(moves) if n > 1 else 0.0
    t_statistic = mean / (spread / (n ** 0.5)) if spread > 0 else None

    return Precedent(
        topic=topic,
        sector=sector,
        cases=n,
        companies=companies,
        median_percent=round(median(moves), 2),
        share_negative=round(sum(1 for m in moves if m < 0) / n, 3),
        low_percent=round(moves[int(n * 0.1)], 1),
        high_percent=round(moves[int(n * 0.9)], 1),
        t_statistic=round(t_statistic, 2) if t_statistic is not None else None,
    )


def measure_precedents(cases: Iterable[Case]) -> PrecedentBase:
    """Group cases by topic. Pure: no database, no prices, no network."""
    by_topic: dict[str, list[Case]] = {}
    for case in cases:
        by_topic.setdefault(case.topic, []).append(case)
    return PrecedentBase(by_topic=by_topic)


__all__ = [
    "DIRECTIONAL_SHARE",
    "DIRECTIONAL_T",
    "MIN_CASES",
    "MIN_COMPANIES",
    "MIN_SECTOR_CASES",
    "TOPICS",
    "TOPIC_LABELS",
    "Case",
    "Precedent",
    "PrecedentBase",
    "measure_precedents",
    "primary_topic",
    "primary_topic_of_signal",
    "topics_of",
    "topics_of_signal",
]
