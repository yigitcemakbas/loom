"""The case file: everything Loom knows about one company, as one argument.

A company page was six independent panels, and nobody assembled them. The
verdict sat above contradictions, which sat above factor percentiles, which sat
above findings, and the reader was left to work out which of the thirty things
in front of them should actually change their mind. That is the synthesis this
product exists to perform, and leaving it undone made Loom a very good
instrument panel with nobody flying the plane.

So this module does one thing: it takes everything already computed about a
company and **ranks it by how much it should move a reader's view**, rather than
grouping it by which subsystem produced it. A reader scanning a list stops near
the top, so the ordering is doing most of the work.

Three rules shape the weighting, and all three are judgement stated openly
rather than anything fitted:

**Disagreement outranks agreement.** When two independent sources point opposite
ways, that is the moment worth a person's attention, and it is the one thing no
screener can produce because it requires having read the documents. A
contradiction ranks above any single reading however extreme.

**Unusual outranks large.** A "major" risk factor from a company that files one
every quarter is telling you about its disclosure habits. The same label from a
company that has never used it is telling you something happened. Where the
statistical engine has scored a finding, rarity lifts it.

**Everything carries what would settle it.** A point a reader cannot check is an
assertion. Where Loom knows what evidence would resolve a question, it says so,
which is what turns an observation into a thesis.

The case file also states plainly what Loom has NOT read, because a page that
looks complete when it is not is the most misleading thing this module could
produce.
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

# Weights are a stated ordering, not a calibrated scale. They decide which of
# roughly thirty items a reader sees first and nothing else; no number here is
# multiplied by a return or fed to anything that claims significance.
WEIGHT_CONTRADICTION = 100
WEIGHT_UNUSUAL_EXTREME = 85
WEIGHT_EXTREME = 70
WEIGHT_MAJOR_FINDING = 60
WEIGHT_LIVE_EVENT = 55
WEIGHT_FINDING = 40
WEIGHT_VALUATION = 35
WEIGHT_CONTEXT = 20

# How many points a case may carry. A ranked list of fifty is a scroll rather
# than an argument: a reader stops near the top, so everything past the first
# dozen is costing attention and returning nothing. The count of what was left
# out is reported, because silently truncating would make a thin case and a
# deep one look identical.
MAX_POINTS = 12

# A matched filing stops being news at this age. The fast path exists because
# the window in which a reaction is worth anything is short, and a two month
# old match is history rather than a reason to act today.
LIVE_EVENT_DAYS = 21

# Factor keys that describe what you pay rather than how the business is doing.
# Separated because they answer a different question and a reader conflating
# them buys a good company at any price.
VALUATION_KEYS = frozenset({
    "earnings_yield", "cash_flow_yield", "sales_yield", "book_to_price",
})


@dataclass(frozen=True)
class CasePoint:
    """One thing that should move a reader's view, and by how much."""

    key: str
    headline: str
    detail: str
    # "for" | "against" | "unclear". Not a score: a case point argues in a
    # direction or explicitly refuses to.
    side: str
    weight: int
    # Where it came from, in words a reader can go and check: a filing, the
    # accounts, the price, Loom's own disagreement with itself.
    source: str
    # The evidence that would resolve it. Absent where Loom genuinely does not
    # know what would, which is more honest than inventing a test.
    settles_it: Optional[str] = None
    # Verbatim, where the point came from a document.
    quote: Optional[str] = None


@dataclass(frozen=True)
class CaseFile:
    ticker: str
    name: str

    # Loom's own read, restated rather than hidden: the case is the argument
    # underneath a verdict, not a replacement for it.
    stance: Optional[str]
    headline: str
    confidence: float

    points: list[CasePoint] = field(default_factory=list)

    # What you would be paying, kept apart from the business case because they
    # answer different questions.
    valuation: list[CasePoint] = field(default_factory=list)

    # What Loom has not read. Stated, because a page that looks complete when
    # it is not is worse than an obviously empty one.
    gaps: list[str] = field(default_factory=list)

    # Set when the reader holds this company, which changes what the case is
    # for: a case against something you own is a reason to act, not to browse.
    held: bool = False

    @property
    def strongest_against(self) -> Optional[CasePoint]:
        """The best argument on the other side, whichever side the verdict took.

        Surfaced separately because a reader who sees only the case for a
        conclusion is reading a sales pitch, and it is the point a decision
        most needs.
        """
        opposing = "against" if (self.stance or "").endswith("positive") else "for"
        candidates = [p for p in self.points if p.side == opposing]
        return max(candidates, key=lambda p: p.weight) if candidates else None


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _side_from_direction(direction: Optional[str]) -> str:
    if direction == "positive":
        return "for"
    if direction == "negative":
        return "against"
    return "unclear"


def build_case(
    *,
    ticker: str,
    name: str,
    brief=None,
    contradictions: Optional[list] = None,
    factors: Optional[list] = None,
    findings: Optional[list] = None,
    events: Optional[list] = None,
    prior=None,
    held: bool = False,
    now: Optional[datetime] = None,
) -> CaseFile:
    """Assemble and rank. Pure: every argument is already-computed data.

    Nothing here recomputes a judgement. If the case file disagreed with the
    verdict or the factor panel it would be a third opinion rather than a
    synthesis, and a reader would have no way to know which to believe.
    """
    now = now or datetime.now(timezone.utc)
    points: list[CasePoint] = []
    valuation: list[CasePoint] = []
    gaps: list[str] = []

    # --- disagreement, first --------------------------------------------
    for contradiction in contradictions or []:
        points.append(CasePoint(
            key=f"contradiction:{contradiction.key}",
            headline=contradiction.headline,
            detail=contradiction.why_it_matters,
            # Deliberately never a side. A contradiction says Loom's own
            # evidence is inconsistent; giving it a direction would invent the
            # synthesis it exists to avoid.
            side="unclear",
            weight=WEIGHT_CONTRADICTION,
            source="Loom's own sources disagreeing",
            settles_it=contradiction.says_worse,
        ))

    # --- the numbers ----------------------------------------------------
    for factor in factors or []:
        percentile = getattr(factor, "percentile", None)
        if percentile is None:
            continue

        is_valuation = factor.key in VALUATION_KEYS
        is_extreme = percentile <= 0.1 or percentile >= 0.9

        # Business measures appear only at the extremes: the middle of a
        # distribution is not saying anything a reader can act on, and a case
        # file is not a data dump.
        #
        # Valuation is the exception, and deliberately. What you would pay is
        # relevant at every level, not only when it is remarkable, and a reader
        # who is shown no price at all will assume price does not matter here.
        if not is_valuation and not is_extreme:
            continue

        good = percentile >= 0.9
        label = getattr(factor, "label", factor.key)
        if is_extreme:
            phrase = "among the best of comparable companies" if good else "among the worst"
        else:
            phrase = getattr(factor, "phrase", None) or "in line with comparable companies"

        point = CasePoint(
            key=f"factor:{factor.key}",
            headline=f"{label}: {phrase}.",
            detail=getattr(factor, "meaning", ""),
            side="for" if good else "against" if is_extreme else "unclear",
            weight=WEIGHT_EXTREME if is_extreme else WEIGHT_VALUATION,
            source=getattr(factor, "source", "reported financial statements"),
            settles_it="The next set of filed accounts.",
        )
        (valuation if is_valuation else points).append(point)

    # --- what Loom read -------------------------------------------------
    for finding in findings or []:
        magnitude = getattr(finding, "market_magnitude", None)
        rate = getattr(finding, "evidence_rate", None)
        unusual = rate is not None and rate < 0.10

        weight = WEIGHT_FINDING
        if unusual:
            # Rarity lifts a finding above a louder but routine one: the same
            # label means something different from a company that uses it every
            # quarter and one that never has.
            weight = WEIGHT_UNUSUAL_EXTREME
        elif magnitude == "major":
            weight = WEIGHT_MAJOR_FINDING

        detail = (getattr(finding, "detail", None) or "").strip()
        headline = (getattr(finding, "summary", "") or "").strip()
        if unusual and rate is not None:
            detail = (
                f"Unusual for this company: only {round(rate * 100)}% of its findings "
                f"are this serious. {detail}"
            ).strip()

        points.append(CasePoint(
            key=f"finding:{getattr(finding, 'id', headline)}",
            headline=headline,
            detail=detail,
            side=_side_from_direction(getattr(finding, "market_direction", None)),
            weight=weight,
            source=_source_label(finding),
            quote=(getattr(finding, "evidence_quote", None) or None),
        ))

    # --- what just happened ---------------------------------------------
    for event in events or []:
        occurred = getattr(event, "occurred_at", None)
        if occurred is None:
            continue
        age = (now - _aware(occurred)).days
        if age > LIVE_EVENT_DAYS:
            continue
        points.append(CasePoint(
            key=f"event:{getattr(event, 'id', occurred)}",
            headline=getattr(event, "headline", ""),
            detail=(
                f"A {getattr(event, 'form', None) or 'filing'} matched what Loom was "
                f"already watching for, {_ago(age)}."
            ),
            side=_side_from_direction(getattr(event, "direction", None)),
            weight=WEIGHT_LIVE_EVENT,
            source="a filing scored against Loom's standing view",
        ))

    # --- what Loom is watching for --------------------------------------
    watch_items = list(getattr(prior, "watch_items", None) or []) if prior else []
    if watch_items:
        topics = [item.get("topic", "") for item in watch_items if item.get("topic")]
        points.append(CasePoint(
            key="prior:watching",
            headline=f"Loom is watching {len(topics)} specific things here.",
            detail=", ".join(topics) + ".",
            side="unclear",
            weight=WEIGHT_CONTEXT,
            source="a standing view built before these events arrived",
            settles_it="The next filing, scored against it automatically.",
        ))
    else:
        gaps.append(
            "Loom has no standing view for this company, so its filings are stored "
            "and read but not scored against anything. A quiet week and an unwatched "
            "week look the same here."
        )

    if not findings:
        gaps.append(
            "Loom has not read this company's filings in depth, so everything above "
            "comes from its reported numbers rather than from what it said."
        )
    if not valuation:
        # Only when the ratios genuinely could not be computed, which means a
        # missing share count or missing accounts. Saying this because a
        # reading was merely unremarkable would be false.
        gaps.append(
            "Loom could not work out what you would be paying for this company, so "
            "nothing above accounts for price."
        )

    # Highest weight first, and within a weight the points that argue a side
    # before the ones that do not, because "here is a reason" is more use at the
    # top of a list than "here is some context".
    points.sort(key=lambda p: (p.weight, p.side != "unclear"), reverse=True)
    valuation.sort(key=lambda p: p.weight, reverse=True)

    # Reported rather than silently dropped. A case built from fifty findings
    # and one built from twelve are different objects, and a reader deserves to
    # know which they are looking at.
    withheld = max(0, len(points) - MAX_POINTS)
    if withheld:
        gaps.append(
            f"{withheld} further findings are not shown here. They ranked below the "
            f"{MAX_POINTS} above and are all on the company's own page."
        )
    points = points[:MAX_POINTS]

    return CaseFile(
        ticker=ticker,
        name=name,
        stance=getattr(brief, "stance", None).value if getattr(brief, "stance", None) else None,
        headline=getattr(brief, "headline", "") or "Loom has not formed a view on this company.",
        confidence=float(getattr(brief, "confidence", 0.0) or 0.0),
        points=points,
        valuation=valuation,
        gaps=gaps,
        held=held,
    )


def _source_label(finding) -> str:
    metadata = getattr(finding, "signal_metadata", None) or {}
    subtype = metadata.get("doc_subtype")
    labels = {
        "10-K": "the annual report",
        "10-Q": "a quarterly report",
        "8-K": "a company announcement",
        "earnings_call": "an earnings call",
        "news": "news coverage",
        "peer_mention": "another company's filing",
    }
    return labels.get(subtype, "a regulatory filing")


def _ago(days: int) -> str:
    if days <= 0:
        return "today"
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days} days ago"
    return f"{days // 7} week{'s' if days >= 14 else ''} ago"


__all__ = [
    "LIVE_EVENT_DAYS",
    "VALUATION_KEYS",
    "WEIGHT_CONTRADICTION",
    "WEIGHT_EXTREME",
    "MAX_POINTS",
    "WEIGHT_UNUSUAL_EXTREME",
    "CaseFile",
    "CasePoint",
    "build_case",
]
