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

from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from typing import Optional

from app.engine.staleness import assess as staleness_of, side_ages

# Weights are a stated ordering, not a calibrated scale. They decide which of
# roughly thirty items a reader sees first and nothing else; no number here is
# multiplied by a return or fed to anything that claims significance.
WEIGHT_CONTRADICTION = 100
WEIGHT_UNUSUAL_EXTREME = 85
# A finding from a filing the market visibly repriced. Above an ordinary
# extreme reading and below a contradiction, and placed there on a stated
# argument: a large move is independent confirmation, from a party with money
# at stake, that the document said something. Nothing else in this list has a
# second source agreeing with it.
WEIGHT_MARKET_MOVED = 78
WEIGHT_EXTREME = 70
WEIGHT_MAJOR_FINDING = 60
WEIGHT_LIVE_EVENT = 55
WEIGHT_FINDING = 40
WEIGHT_VALUATION = 35
WEIGHT_PRICE_CONTEXT = 30
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
    # True where this is the disclosure its kind of document always contains,
    # and therefore carried no weight in the verdict.
    #
    # Without it, expanding the withheld list makes a correctly computed
    # verdict look unsupported. Apple reads positive, and the points below the
    # cut run 26 against to 14 for: a reader counting them concludes the
    # verdict contradicts its own evidence. It does not, because most of those
    # 26 are risk factors that every annual report contains and the stance
    # already scored at approximately zero. The reader had no way to see that.
    routine: bool = False


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

    # The ranked points that did not make the cut, in the same order and the
    # same shape. Returned rather than discarded: every case file was already
    # announcing that thirty to sixty further findings existed and offering no
    # way to reach them, which is a worse failure than either showing them or
    # not mentioning them. A reader told that evidence is being withheld and
    # given no means to see it has been handed a reason to distrust the twelve
    # that were shown.
    #
    # Still ranked, still capped by `MAX_POINTS`, and still behind a deliberate
    # action in the interface: the ordering is the product, and a list of fifty
    # opened by default would undo it.
    withheld: list[CasePoint] = field(default_factory=list)

    # What you would be paying, kept apart from the business case because they
    # answer different questions.
    valuation: list[CasePoint] = field(default_factory=list)

    # What the price has already done. A third block rather than points mixed
    # into the argument, because it is not an argument: it is the condition a
    # reader is reading everything else in. The same risk factor means
    # different things at an all-time high and after a forty percent fall, and
    # nothing in the filing says which of those the reader is looking at.
    price: list[CasePoint] = field(default_factory=list)

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
        stance = self.stance or ""
        # A verdict has to point somewhere before anything can point against it.
        #
        # Without this, every stance that is not positive was treated as
        # negative, so a company Loom had explicitly declined to judge showed
        # "the best argument the other way" above its own "not enough read yet"
        # — offering a rebuttal to a claim it had just refused to make. On
        # Coca-Cola that rendered as a *favourable* point presented as the
        # objection to it, which is the exact opposite of what the label says.
        if not (stance.endswith("positive") or stance.endswith("negative")):
            return None
        opposing = "against" if stance.endswith("positive") else "for"
        # Searched across the withheld points too. This is the one slot on the
        # page that must not be decided by the twelve-point cap: a company
        # whose twelve strongest points all argue one way is exactly the case
        # where the best objection has been ranked off the list, and showing no
        # counterpoint there would read as "there is no argument the other way"
        # rather than "it did not fit".
        candidates = [p for p in (*self.points, *self.withheld) if p.side == opposing]
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
    standing=None,
    moves: Optional[dict] = None,
    precedents: Optional[dict] = None,
    sector_move=None,
    routine_ids: Optional[set] = None,
) -> CaseFile:
    """Assemble and rank. Pure: every argument is already-computed data.

    Nothing here recomputes a judgement. If the case file disagreed with the
    verdict or the factor panel it would be a third opinion rather than a
    synthesis, and a reader would have no way to know which to believe.
    """
    now = now or datetime.now(timezone.utc)
    points: list[CasePoint] = []
    valuation: list[CasePoint] = []
    price_points: list[CasePoint] = []
    gaps: list[str] = []
    moves = moves or {}
    precedents = precedents or {}
    routine_ids = routine_ids or set()
    pending_precedents: dict[str, object] = {}

    # --- disagreement, first --------------------------------------------
    for contradiction in contradictions or []:
        points.append(CasePoint(
            key=f"contradiction:{contradiction.key}",
            headline=contradiction.headline,
            # The plain version here, the expert version on the contradictions
            # panel. The case file is the page a reader arrives at, and in the
            # paired agent trial the contradictions were both the most-cited
            # thing Loom produced and the thing one reader could not parse at
            # all, which is a combination worth taking seriously.
            detail=getattr(contradiction, "plain", "") or contradiction.why_it_matters,
            # Deliberately never a side. A contradiction says Loom's own
            # evidence is inconsistent; giving it a direction would invent the
            # synthesis it exists to avoid.
            side="unclear",
            weight=WEIGHT_CONTRADICTION,
            source="Loom's own sources disagreeing",
            # The observation that would resolve it. This slot previously held
            # the pessimistic side of the disagreement, which put "the cash
            # conversion is among the worst of its peers" under the heading
            # "What would settle it", answering a question nobody asked.
            settles_it=getattr(contradiction, "settled_by", "") or None,
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

        move = moves.get(str(getattr(finding, "id", "")))

        weight = WEIGHT_FINDING
        if unusual:
            # Rarity lifts a finding above a louder but routine one: the same
            # label means something different from a company that uses it every
            # quarter and one that never has.
            weight = WEIGHT_UNUSUAL_EXTREME
        elif move is not None and move.is_material:
            # The market repriced the filing this came from. That is the only
            # confirmation in this whole list that comes from outside Loom.
            #
            # Note what is deliberately absent: there is no matching penalty for
            # a filing the market ignored. A disclosure nobody has priced is the
            # only kind Loom can add anything to, and a rule that quietly buried
            # those would delete the reason to read filings at all.
            weight = WEIGHT_MARKET_MOVED
        elif magnitude == "major":
            weight = WEIGHT_MAJOR_FINDING

        detail = (getattr(finding, "detail", None) or "").strip()
        headline = (getattr(finding, "summary", "") or "").strip()
        if unusual and rate is not None:
            detail = (
                f"Unusual for this company: only {round(rate * 100)}% of its findings "
                f"are this serious. {detail}"
            ).strip()

        # Leads the detail rather than following it. A reader who stops after
        # one sentence should have learned that the evidence is older than its
        # date implies, because that changes how the rest of the sentence should
        # be read.
        stale = staleness_of(finding)
        if stale.note:
            detail = f"{stale.note} {detail}".strip()

        # What has followed comparable disclosures elsewhere. Appended last,
        # because it is calibration rather than content: it tells a reader how
        # much weight the sentence above deserves, which is only useful once
        # they have read the sentence.
        #
        # Most of the time this says the comparable cases point nowhere, and
        # that is the point. A risk factor is written in the register of a
        # serious problem whatever it describes, and a reader with no way to
        # calibrate that reads every one of them as bad news. The genre
        # correction fixed this for the engine; the engine is not the only one
        # doing the arithmetic.
        precedent = precedents.get(str(getattr(finding, "id", "")))


        point_key = f"finding:{getattr(finding, 'id', headline)}"
        if precedent is not None:
            # Attached now, applied after sorting. A reader should meet a
            # topic's calibration the first time they meet the topic, and which
            # row that is depends on the ranking, which has not happened yet.
            pending_precedents[point_key] = precedent

        points.append(CasePoint(
            key=point_key,
            headline=headline,
            detail=detail,
            side=_side_from_direction(getattr(finding, "market_direction", None)),
            weight=weight,
            # The move rides on the source line as a clause rather than on the
            # detail as a sentence. One annual report yields forty findings and
            # all of them measure the same fortnight, so the full sentence
            # appeared forty times on one page; the page states it once, in the
            # price block, and each row carries the fact without the paragraph.
            source=_source_label(finding) + (
                f", {move.short}" if move is not None and move.is_material else ""
            ),
            quote=(getattr(finding, "evidence_quote", None) or None),
            routine=str(getattr(finding, "id", "")) in routine_ids,
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
            headline=_without_ticker(getattr(event, "headline", ""), ticker),
            detail=(
                f"A {getattr(event, 'form', None) or 'filing'} matched what Loom was "
                f"already watching for, {_ago(age)}."
            ),
            side=_side_from_direction(getattr(event, "direction", None)),
            weight=WEIGHT_LIVE_EVENT,
            source="a filing scored against Loom's standing view",
        ))

    # --- the condition all of it is read in -----------------------------
    #
    # The most recent filing the market actually repriced, stated once. Most
    # recent rather than largest: the question a reader has is what the market
    # made of the latest disclosure, not which of the past year's filings moved
    # it furthest.
    material = [m for m in moves.values() if m.is_material]
    if material:
        latest = max(material, key=lambda m: m.as_of)
        price_points.append(CasePoint(
            key="price:reaction",
            headline=latest.standalone,
            detail=(
                "This is the only confirmation on this page that comes from outside "
                "Loom. It does not say the market was right, and it does not say which "
                "part of the filing it was responding to. It says the document was read "
                "by people with money at stake and changed what they would pay."
            ),
            side="unclear",
            weight=WEIGHT_PRICE_CONTEXT + 1,
            source="the price history Loom stores",
            settles_it="Nothing. It has already happened.",
        ))

    # Whether the move belonged to this company or to everything around it.
    #
    # The company-level measure cannot answer this, and not by oversight: it
    # reports the filer's return minus the market's, which removes any effect
    # that hit a whole group. One company writing about AI capital spending and
    # the whole complex repricing shows up there as nothing having happened.
    if sector_move is not None:
        price_points.append(CasePoint(
            key="price:sector",
            headline=sector_move.summary,
            detail=(
                "A filing can move the industry it belongs to rather than only the "
                "company that made it. Where the group moved and this company moved "
                "with it, what is being read is an industry event that this company "
                "happens to have disclosed. Where the company moved and the group did "
                "not, it is this company's news."
            ),
            side="unclear",
            weight=WEIGHT_PRICE_CONTEXT + 2,
            source="the price histories of this company's sector",
            settles_it="Nothing. It has already happened.",
        ))

    if standing is not None:
        price_points.append(CasePoint(
            key="price:standing",
            headline=standing.summary,
            detail=(
                "Where a share has already traded decides how much of this is news. "
                "A concern disclosed at a high has not been paid for yet; the same "
                "concern after a long fall may already have been."
            ),
            # Never a side, and this is the line the module has to hold. Price
            # direction is not an argument for or against a company: treating a
            # rise as a point in its favour is momentum wearing a verdict's
            # clothes, which is predictive in precisely the sense this project
            # refuses.
            side="unclear",
            weight=WEIGHT_PRICE_CONTEXT,
            source="the price history Loom stores",
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

    # Whether the two halves of the case are the same age. A case built from
    # current concerns and year-old positives is not a balance of views, it is a
    # comparison across time, and nothing in the list above reveals that: each
    # point shows the date of the document it came from, and both sides usually
    # come from the same filings.
    if findings:
        ages = side_ages(findings, now=now)
        if ages.note:
            gaps.append(ages.note)

    if not findings:
        gaps.append(
            "Loom has not read this company's filings in depth, so everything above "
            "comes from its reported numbers rather than from what it said."
        )
    if standing is None:
        gaps.append(
            "Loom has no price history for this company, so nothing above accounts for "
            "what the market has already done with this information."
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
    price_points.sort(key=lambda p: p.weight, reverse=True)
    # One story, one row. Three filings matching the same standing expectation
    # produced three identical rows, which was invisible while everything past
    # the twelfth was behind a count and is the first thing a reader sees now
    # that the rest is reachable. Collapsed after sorting, so the copy that
    # survives is the highest-weighted one.
    points = _collapse(points)
    points = _apply_precedents(points, pending_precedents)
    valuation.sort(key=lambda p: p.weight, reverse=True)

    # Split rather than truncated. A case built from fifty findings and one
    # built from twelve are different objects, and the count alone was never
    # enough: it told a reader that evidence existed, named no way to see it,
    # and left them to decide whether the twelve above were the twelve that
    # mattered or the twelve that happened to rank.
    withheld = points[MAX_POINTS:]
    points = points[:MAX_POINTS]

    return CaseFile(
        ticker=ticker,
        name=name,
        stance=getattr(brief, "stance", None).value if getattr(brief, "stance", None) else None,
        headline=getattr(brief, "headline", "") or "Loom has not formed a view on this company.",
        confidence=float(getattr(brief, "confidence", 0.0) or 0.0),
        points=points,
        withheld=withheld,
        valuation=valuation,
        price=price_points,
        gaps=gaps,
        held=held,
    )


def _without_ticker(headline: str, ticker: str) -> str:
    """Strip a leading ticker from a headline shown on that company's own page.

    Event headlines carry the ticker because they are written to be read in a
    feed covering every company. Here the ticker is the heading of the page, so
    repeating it spends the most valuable characters in the row on something the
    reader already knows.
    """
    prefix = f"{ticker}: "
    return headline[len(prefix):] if headline.startswith(prefix) else headline


def _apply_precedents(points: list[CasePoint], pending: dict) -> list[CasePoint]:
    """Attach each topic's calibration to the first row that needs it.

    Applied after ranking rather than during assembly, and once per topic
    rather than once per row. Both halves matter. Apple's case carries fifteen
    separate findings about costs and margins, so attaching the calibration to
    each one printed the same two-line paragraph fifteen times down a single
    page, which is the sentence repeated rather than the sentence read. And a
    reader should meet it on the *highest-ranked* row of that topic, which is
    not known until the ranking exists.
    """
    if not pending:
        return points
    seen: set[str] = set()
    out: list[CasePoint] = []
    for point in points:
        precedent = pending.get(point.key)
        if precedent is not None and precedent.topic not in seen:
            seen.add(precedent.topic)
            point = replace(point, detail=f"{point.detail} {precedent.summary}".strip())
        out.append(point)
    return out


def _collapse(points: list[CasePoint]) -> list[CasePoint]:
    """One row per story, keeping the first occurrence.

    Identity is the headline rather than the key, because the duplicates are
    distinct records saying the same thing: three separate filings can each
    match the same standing expectation, and each one arrives with its own id.
    Deduplicating on the key would leave all three.
    """
    seen: set[str] = set()
    kept: list[CasePoint] = []
    for point in points:
        marker = point.headline.strip().lower()
        if marker and marker in seen:
            continue
        seen.add(marker)
        kept.append(point)
    return kept


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
    "WEIGHT_MARKET_MOVED",
    "WEIGHT_PRICE_CONTEXT",
    "MAX_POINTS",
    "WEIGHT_UNUSUAL_EXTREME",
    "CaseFile",
    "CasePoint",
    "build_case",
]
