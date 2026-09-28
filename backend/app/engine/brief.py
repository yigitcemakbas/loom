"""Synthesis: many findings in, one answer out.

This is the module the whole product exists for. Everything upstream, filings,
transcripts, news, insider trades, extraction, diffing, clustering, produces
*findings*. A pile of findings is not a decision aid: a reader faced with
forty-five separate sentences about Apple learns less than one faced with a
single sentence that says which way the evidence leans and why. That gap is
what this module closes.

**Deliberately deterministic.** No model call. Three reasons, in order of
importance:

1. The judgement here is arithmetic, not language. Which way does the weighted
   evidence lean, do independent sources agree, what is new since last time.
   Those are computable, and computing them is more reliable than asking.
2. It must work when the provider is rate limited or unconfigured. The single
   most important screen in the product cannot be the one that goes blank when
   a free tier runs out.
3. It is auditable. A reader can be shown exactly which findings produced a
   stance, which is not true of a synthesised paragraph.

An optional language pass can rewrite the headline more fluently later; the
stance, the drivers, and the evidence do not depend on it.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from app.models.brief import Stance
from app.engine.direction import can_be_directional
from app.engine.direction import label as direction_label
from app.engine.disclosure import incidence_excess
from app.engine.disclosure import (
    informative_count,
    RECURRENCE_DISCOUNT,
    UNINFORMATIVE_FLOOR,
    DisclosureNorms,
    document_key,
    effective_findings,
    parity_weights,
    restated,
    routine_share,
)
from app.engine.staleness import (
    STALE_DISCOUNT,
    assess as staleness_of,
    effective_age_days,
    side_ages,
    stale_ids,
)
from app.engine.statistics.engine import ANOMALY_RATE
from app.engine.statistics.features import MAGNITUDE_WEIGHT
from app.models.signal import Signal, SignalType

# Bumped when the synthesis rules change, so stored briefs can be regenerated
# deliberately rather than drifting silently.
ENGINE_VERSION = "2026-09-28.2"

WINDOW_DAYS = 90

# When nothing falls inside the window, the newest evidence is used instead,
# up to this age. The window exists so a company under continuous coverage is
# judged on what is current rather than on last year, which is right. It was
# also silently discarding everything Loom knew about a company read once:
# findings carry the date of the filing they came from, and a 10-K read in
# September is dated to February, so eleven companies with seven to nine real
# findings each reported "not enough has been analysed yet" while holding a
# full annual report.
#
# An analyst reading a 10-K in September does not discard it for being eight
# months old, they say so. So does this: `evidence.newest_finding_days` carries
# the age, and the headline says what the verdict rests on.
FALLBACK_WINDOW_DAYS = 400

# How much each magnitude counts toward the stance. A "major" finding should
# not be outvoted by two "minor" ones. Imported rather than declared: the
# statistics package needs the same vocabulary, and two copies of a scoring
# table are two chances to disagree.
_MAGNITUDE_WEIGHT = MAGNITUDE_WEIGHT


@dataclass(frozen=True)
class Horizon:
    """A holding period, and what counts as evidence for it.

    A verdict with no stated horizon is close to meaningless, because the same
    evidence points opposite ways depending on how long you intend to hold. A
    regulatory clampdown is ruinous for a one-week trade and may be irrelevant
    to a five-year one; a capex cycle is the reverse. "Leaning negative" with
    no period attached invites a reader to supply their own, which is exactly
    the ambiguity this resolves.

    Two axes, and both matter. `window_days` is how far back evidence is still
    admissible: a one-week view cannot be built from a filing eight months old.
    The weights are how much a finding counts given how long the extraction
    said its effect would last, which Loom has always recorded on every signal
    and, until now, never used for anything.
    """

    key: str
    label: str
    window_days: int
    # market_horizon -> multiplier
    weights: dict[str, float]
    # Fewer findings than this and the horizon is reported as unsupported
    # rather than answered from two observations.
    min_findings: int


# Unset horizons are given the middle weight rather than dropped. Just under
# half the stored corpus predates horizon extraction, and excluding it would
# empty every view; treating it as mid-duration matches how `market_magnitude`
# already handles the same gap.
_UNSET = "unset"

HORIZONS: dict[str, Horizon] = {
    "1w": Horizon(
        key="1w", label="one week", window_days=45,
        weights={"near_term": 1.0, "multi_quarter": 0.25, "structural": 0.05, _UNSET: 0.3},
        min_findings=2,
    ),
    "1m": Horizon(
        key="1m", label="one month", window_days=120,
        weights={"near_term": 1.0, "multi_quarter": 0.6, "structural": 0.2, _UNSET: 0.5},
        min_findings=2,
    ),
    "1y": Horizon(
        key="1y", label="one year", window_days=400,
        weights={"near_term": 0.35, "multi_quarter": 1.0, "structural": 0.85, _UNSET: 0.7},
        min_findings=3,
    ),
    "5y": Horizon(
        key="5y", label="five years", window_days=1825,
        weights={"near_term": 0.1, "multi_quarter": 0.5, "structural": 1.0, _UNSET: 0.5},
        min_findings=3,
    ),
}

DEFAULT_HORIZON = "1y"


def horizon_weight(signal, horizon: Horizon) -> float:
    return horizon.weights.get(signal.market_horizon or _UNSET, horizon.weights[_UNSET])

# Stance thresholds on the weighted mean of finding directions (-1..1).
_STRONG = 0.55
_LEAN = 0.15

# Below this share of directional findings, a company is "quiet" rather than
# mixed: mixed implies a real tug of war, quiet means nothing much was said.
_MIN_DIRECTIONAL_SHARE = 0.25

# Findings carry a market-impact assessment only if they were analysed after
# that feature existed. Below this share of assessed findings we must say the
# company has not been read yet, NOT that it is quiet: reporting an unanalysed
# company as calm is the most misleading thing this module could do.
_MIN_ASSESSED_SHARE = 0.4

# Overclaiming is the fastest way to make a tool like this untrustworthy. One
# insider cluster from one source is not "serious concerns"; it is one finding.
# A confident verdict has to rest on several findings agreeing across more than
# one kind of source, otherwise the stance is softened a step.
# How many findings must have told Loom something before it will lean at all.
# A count, not a weighted sum: the rule is "one finding cannot carry a verdict",
# and it is measured in findings because that is the unit it was written in.
_MIN_INFORMATIVE_FINDINGS = 2
# The continuous strength, after clustering and repetition, above which a strong
# verdict may stand unsoftened. Thin evidence now softens a verdict rather than
# erasing it, which is the difference between "Loom has little to go on here and
# leans negative" and a blank.
_MIN_STRENGTH_FOR_STRONG = 3.0
_MIN_SOURCES_FOR_STRONG = 2

# Two findings above this token overlap are the same story told twice. Without
# this, one theme that appears in a filing, a call, and three news items fills
# every driver slot and hides everything else.
_DUPLICATE_OVERLAP = 0.4

MAX_DRIVERS = 3

# Below this, the routine share is not worth a clause: every corporate document
# contains some required language, and reporting "about 12% of this was
# boilerplate" on every company would train a reader to skip the sentence that
# matters on the companies where the share is most of the record.
_ROUTINE_WORTH_SAYING = 0.35

# Findings whose type is inherently about the company's own disclosures. Used
# to decide whether we have enough to say anything at all.
_SUBSTANTIVE_TYPES = {
    SignalType.NEW_RISK_FACTOR,
    SignalType.QOQ_ANOMALY,
    SignalType.GUIDANCE_CHANGE,
    SignalType.EMERGING_PATTERN,
    SignalType.INSIDER_ACTIVITY,
    SignalType.SHORT_INTEREST_SPIKE,
    SignalType.NOTABLE_QUOTE,
    # Added when direction became documentary, and the reason is structural
    # rather than a preference. Every other direction a substantive finding can
    # carry is negative: a risk factor appearing, a risk diff, an insider sale,
    # a short-interest spike. Quotes and guidance changes carry none. Excluding
    # the one bidirectional observation Loom actually records would leave the
    # stance with no positive channel whatsoever, which is not caution, it is a
    # sign error — and it is the same structural negative bias that had reader
    # agents shorting into two +22% quarters.
    #
    # A shift in the tone of the company's own disclosure is a property of the
    # text, comparable between filings, and not a claim about price. It belongs
    # in the same class as "the risk section grew". It stays the lowest-weighted
    # evidence type in priority.py, which is where its being a language
    # judgement is accounted for.
    #
    # The proper fix is a resolved-risk-factor signal, which needs the diff to
    # run both ways and a migration to add the type. Until that exists this is
    # what keeps the stance from being negative by construction.
    SignalType.SENTIMENT_SHIFT,
}

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "could", "may",
    "would", "will", "have", "has", "are", "was", "were", "its", "their", "which",
    "company", "companys", "risk", "risks", "increase", "increased", "increasing",
    "reduce", "reduced", "reducing", "impact", "material", "materially", "adverse",
    "adversely", "affect", "affected", "significant", "significantly", "continue",
    "continued", "results", "operations", "financial", "condition", "business",
}

# Plain-language names for source kinds. "10-Q" means nothing to a reader who
# does not already know what a 10-Q is.
SOURCE_LABELS = {
    "10-K": "annual report",
    "10-Q": "quarterly report",
    "8-K": "company announcement",
    "earnings_call": "earnings call",
    "news": "news coverage",
    "insider": "insider trading records",
    "filing": "regulatory filing",
}

# What each stance is called, in words that describe what it actually measures.
#
# These were written when the stance was an absolute average of finding
# directions, and "Leaning positive" was then a fair description of it. It is
# not one now. Since the genre correction the stance is a *residual*: how far
# this company's disclosure departs from what documents of its kind normally
# carry. Microsoft reads positive while holding fifteen concerns against twelve
# positives, because most of those concerns are the risk factors every annual
# report contains.
#
# So the old labels asserted the opposite of the counts printed beside them.
# The headline was migrated to the new meaning and these were not, which left
# one screen saying both things.
STANCE_LABELS = {
    Stance.STRONG_NEGATIVE: "Reads much worse than usual",
    Stance.NEGATIVE: "Reads worse than usual",
    Stance.MIXED: "Reads about as usual",
    Stance.POSITIVE: "Reads better than usual",
    Stance.STRONG_POSITIVE: "Reads much better than usual",
    Stance.QUIET: "Nothing notable",
    Stance.INSUFFICIENT: "Not enough read yet",
}


@dataclass
class Driver:
    title: str
    detail: str
    direction: str
    magnitude: str
    sources: list[str] = field(default_factory=list)
    signal_ids: list[str] = field(default_factory=list)
    # How rare this severity is for this company, from the statistical engine.
    # None means no baseline existed, which is not the same as ordinary, so the
    # interface must not render the two alike.
    evidence_rate: float | None = None
    evidence_sample_size: int | None = None
    # True when the finding supplied its own short label. A clipped sentence
    # cannot be dropped into the middle of a headline and still read as English.
    is_label: bool = False
    # How old this evidence actually is, counting the period the quote describes
    # rather than the date of the document that carried it. A filing published
    # this month can quote a call from two years ago, and the finding's own
    # timestamp gives a reader no way to tell.
    age_days: int | None = None
    # Set only where that gap is large enough to change how the evidence should
    # be read. None the vast majority of the time, which is what keeps it worth
    # reading when it appears.
    stale_note: str | None = None


@dataclass
class Brief:
    stance: Stance
    headline: str
    confidence: float
    drivers: list[Driver]
    # The strongest finding arguing the other way. Shown deliberately: a
    # one-sided card invites the reader to assume the other side was never
    # considered, and the single best objection is the thing a decision most
    # needs to survive.
    counterpoint: Driver | None
    what_changed: str | None
    source_types: list[str]
    signal_count: int
    evidence: dict


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]{4,}", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def source_types_of(signals: list[Signal]) -> set[str]:
    return {_source_of(s) for s in signals}


def _source_of(signal: Signal) -> str:
    """Which kind of source a finding came from, in plain terms."""
    if signal.signal_type == SignalType.INSIDER_ACTIVITY:
        return "insider"
    if signal.signal_type == SignalType.SHORT_INTEREST_SPIKE:
        return "insider"
    subtype = (signal.signal_metadata or {}).get("doc_subtype")
    return subtype or "filing"


def _title_of(signal: Signal) -> tuple[str, bool]:
    """The short label an extracted finding already carries.

    Risk findings arrive as "label: explanation"; splitting on the first colon
    recovers a usable heading without asking a model to invent one.
    """
    summary = (signal.summary or "").strip()
    head, sep, _ = summary.partition(":")
    if sep and 3 <= len(head) <= 70:
        return head.strip(), True
    if len(summary) <= 70:
        return summary.rstrip(" .,"), False
    # Cut on a word boundary; truncating mid-word ("...supply bottleneck")
    # silently changes the meaning of the phrase.
    clipped = summary[:70].rsplit(" ", 1)[0]
    return clipped.rstrip(" .,") + "…", False


def _weighted_direction(
    signals: list[Signal],
    horizon: Horizon | None = None,
    *,
    norms: DisclosureNorms | None = None,
    parity: dict[str, float] | None = None,
    restated_ids: set[str] | None = None,
    stale_ids: set[str] | None = None,
) -> tuple[float, float, dict[str, int], dict]:
    """Return (mean_direction, total_weight, counts, workings). Mean is -1..1.

    With `norms` supplied the mean is a **residual**, not a level: each finding
    contributes how far its direction sits from what findings of its kind, from
    that kind of document, normally carry. This is the correction described in
    engine/disclosure.py, and it is what stops a company being marked down for
    the number of risk factors it publishes. A negative risk factor in an
    annual report contributes approximately nothing, because every annual
    report's risk section is negative; a negative guidance change contributes
    almost all of its weight, because guidance changes are not.

    Without `norms` the behaviour is the original one, a plain weighted mean of
    directions. Both paths are kept because the residual is only meaningful
    against a measured corpus, and a caller that has not measured one should
    get the honest older answer rather than a residual against nothing.
    """
    counts = {"positive": 0, "negative": 0, "neutral": 0}
    total = 0.0
    weighted = 0.0
    expected_total = 0.0
    restated_count = 0
    stale_count = 0
    uninformative = 0
    for s in signals:
        # What the document did, not what a market might do about it.
        direction = direction_label(s)
        if direction not in ("positive", "negative", "neutral"):
            continue
        counts[direction] += 1
        weight = _MAGNITUDE_WEIGHT.get(s.market_magnitude or "moderate", 1.0)
        weight *= max(s.priority or 0.0, 0.05)
        if horizon is not None:
            # A structural finding barely moves a one-week view, and a
            # near-term one barely moves a five-year view. This is the whole
            # mechanism by which the same evidence yields different verdicts.
            weight *= horizon_weight(s, horizon)
        if parity is not None:
            # Forty findings from one filing are one document read closely,
            # not forty independent observations.
            weight *= parity.get(str(s.id), 1.0)
        if restated_ids and str(s.id) in restated_ids:
            # The company said this before, in a different filing. Carrying a
            # risk forward is weak evidence it is still live, and nothing more.
            weight *= RECURRENCE_DISCOUNT
            restated_count += 1
        if stale_ids and str(s.id) in stale_ids:
            # The quote is about a period years before the filing that carried
            # it. Detecting that without acting on it would produce a page
            # warning about evidence its own headline was computed from.
            weight *= STALE_DISCOUNT
            stale_count += 1

        sign = 1.0 if direction == "positive" else -1.0 if direction == "negative" else 0.0
        contribution = sign
        if norms is not None:
            # Neutral findings are excluded rather than given a residual.
            # "Neutral" here mostly means the extraction declined to call a
            # direction, and against a genre whose expectation is near -1 a
            # declined call would score as a strong positive, which would let
            # one unjudged risk factor carry a verdict. A company that is
            # mostly neutral is caught by `directional_share` instead.
            if sign == 0.0:
                continue
            excess = norms.excess_for(s)
            if excess is None:
                continue
            if abs(excess) < UNINFORMATIVE_FLOOR:
                # Said nothing, so it neither votes nor dilutes. See
                # disclosure.UNINFORMATIVE_FLOOR for why the second half of
                # that sentence is the one that matters.
                uninformative += 1
                continue
            contribution = excess
            expected_total += weight * norms.expected_for(s).expected

        weighted += weight * contribution
        total += weight

    # No rescaling, deliberately. The quantity below already runs -1 to +1, so
    # the stance thresholds apply unchanged. They stay universal on purpose: the
    # measurement is already relative to this company's sector and to the genre
    # of each document, and a per-company threshold on top of a per-company
    # measurement would be dynamism applied twice.
    mean = weighted / total if total else 0.0

    # The stance comes from incidence, not from the per-finding mean above.
    #
    # The per-finding mean is kept and reported because it is what the counts
    # describe, but it can no longer carry a verdict: direction is now a property
    # of the finding's type, so the per-finding residual against a genre whose
    # expectation equals that direction is identically zero. Measured on the
    # corpus, mean absolute excess for a new risk factor was 0.0018.
    #
    # What varies is volume. A filing that added fourteen new risk factors where
    # its sector adds four is saying something; one that added four is not. That
    # is the quantity the stance is now computed from, and it is bidirectional
    # without a resolved-risk signal, because fewer concerns than the genre
    # expects reads better.
    incidence_residual = None
    incidence_workings: dict | None = None
    if norms is not None and getattr(norms, "incidence", None) is not None:
        sector = None
        for s in signals:
            company = getattr(s, "company_id", None)
            if company is not None:
                sector = norms.sector_of.get(str(company))
                break
        incidence_residual, incidence_workings = incidence_excess(
            signals, norms.incidence, sector=sector
        )
        if incidence_residual is not None:
            mean = incidence_residual
    workings = {
        "restated": restated_count,
        "stale": stale_count,
        "uninformative": uninformative,
        "expected_mean": round(expected_total / total, 3) if total else None,
        "raw_excess": round(mean, 3) if norms is not None else None,
        # The incidence workings, so a stance can be audited against the filings
        # it was computed from: observed against expected directional counts, per
        # document. This is the whole basis of the verdict and a reader is
        # entitled to see the arithmetic.
        "incidence_residual": (round(incidence_residual, 4)
                               if incidence_residual is not None else None),
        "incidence": incidence_workings if incidence_residual is not None else None,
    }
    return mean, total, counts, workings


def _pick_counterpoint(
    signals: list[Signal],
    stance_direction: str | None,
    *,
    norms: DisclosureNorms | None = None,
) -> Driver | None:
    """The strongest finding that argues against the stance.

    Ranked by informativeness before priority for the same reason drivers are.
    The best argument the other way is the one point a decision most needs, and
    filling it with a sentence every company in the index also filed would
    waste the most valuable slot on the screen.
    """
    if stance_direction not in ("positive", "negative"):
        return None
    opposite = "negative" if stance_direction == "positive" else "positive"
    against = [s for s in signals if direction_label(s) == opposite]
    if not against:
        return None

    def rank(signal: Signal) -> tuple:
        excess = norms.excess_for(signal) if norms is not None else None
        return (abs(excess) if excess is not None else 0.0, signal.priority or 0.0)

    best = max(against, key=rank)
    drivers = _build_drivers([best], signals)
    return drivers[0] if drivers else None


def is_unusual_for_company(signal) -> bool:
    """Whether this finding's severity is rare for the company that produced it.

    One definition, read from the stored rate rather than recomputed, so the
    brief and the interface cannot disagree about which findings are unusual.
    A finding with no stored rate is not unusual: it has no baseline to be
    unusual against, and treating unscored as remarkable would let thin history
    manufacture alarm.
    """
    rate = getattr(signal, "evidence_rate", None)
    return rate is not None and rate < ANOMALY_RATE


def _pick_drivers(
    signals: list[Signal],
    stance_direction: str | None = None,
    *,
    norms: DisclosureNorms | None = None,
) -> list[Driver]:
    """Findings that explain the stance, with repeats of one story collapsed.

    Selection is filtered by the stance's own direction before ranking. Picking
    purely by priority produced cards that contradicted themselves: Apple's
    verdict read "more concerns than positives" (17 negative findings against
    7) while listing three positive drivers underneath, because those three
    happened to score highest. A verdict whose stated evidence argues the other
    way gives a reader no reason to believe any of it.

    The opposing side is not hidden, it is returned last and labelled as a
    counterpoint, which is more useful than either burying it or leading with
    it (see `counterpoint`).
    """
    aligned = signals
    if stance_direction in ("positive", "negative"):
        matching = [s for s in signals if direction_label(s) == stance_direction]
        # Fall back to everything if the stance came from weighting rather than
        # a clear majority, so a brief never ends up with no drivers at all.
        aligned = matching or signals

    # Findings whose severity is unusual for this company outrank routine ones.
    # Priority alone ranks by the finding's own attributes, which means a
    # company that files a "major" risk every quarter fills all three driver
    # slots with its house style while the one genuinely uncharacteristic
    # finding sits below the fold. This is the statistical engine's first job
    # with teeth: it reorders what a reader sees, and it still cannot change
    # what the verdict says.
    # Informativeness comes first, where it has been measured. A risk factor
    # that says what every annual report says should not take one of three
    # driver slots from a finding that told Loom something, and priority alone
    # cannot tell them apart: a boilerplate concentration risk and a genuine
    # guidance cut can carry the same confidence and the same magnitude.
    def rank(signal: Signal) -> tuple:
        excess = norms.excess_for(signal) if norms is not None else None
        informative = abs(excess) >= UNINFORMATIVE_FLOOR if excess is not None else True
        return (
            informative,
            is_unusual_for_company(signal),
            abs(excess) if excess is not None else 0.0,
            signal.priority or 0.0,
        )

    ordered = sorted(aligned, key=rank, reverse=True)
    chosen: list[Signal] = []
    seen: list[set[str]] = []

    for signal in ordered:
        if not signal.summary:
            continue
        tokens = _tokens(signal.summary)
        if any(_overlap(tokens, prior) >= _DUPLICATE_OVERLAP for prior in seen):
            # Same story already represented; fold this in as extra support
            # rather than spending a driver slot on it.
            continue
        chosen.append(signal)
        seen.append(tokens)
        if len(chosen) >= MAX_DRIVERS:
            break

    return _build_drivers(chosen, signals)


def _build_drivers(chosen: list[Signal], population: list[Signal]) -> list[Driver]:
    """Turn selected findings into drivers, recording every source that showed
    the same story."""
    def _detail_for(signal: Signal, title: str) -> str:
        """Body text with the heading stripped, so a driver does not read
        'Tariffs / Tariffs: imposes additional cost friction'."""
        detail = (signal.detail or signal.summary or "").strip()
        prefix = f"{title}:"
        if detail.lower().startswith(prefix.lower()):
            detail = detail[len(prefix):].strip()
        return detail

    drivers: list[Driver] = []
    for signal in chosen:
        tokens = _tokens(signal.summary)
        supporting = [
            s for s in population
            if s.id != signal.id and _overlap(_tokens(s.summary or ""), tokens) >= _DUPLICATE_OVERLAP
        ]
        sources = {_source_of(signal), *(_source_of(s) for s in supporting)}
        title, is_label = _title_of(signal)
        drivers.append(
            Driver(
                title=title,
                is_label=is_label,
                detail=_detail_for(signal, title),
                # "unassessed" rather than "neutral": the reader must be able to
                # tell a judged-as-balanced finding from an unjudged one.
                direction=direction_label(signal),
                magnitude=signal.market_magnitude or "moderate",
                sources=sorted(sources),
                signal_ids=[str(signal.id), *[str(s.id) for s in supporting]],
                evidence_rate=signal.evidence_rate,
                evidence_sample_size=signal.evidence_sample_size,
                age_days=effective_age_days(signal),
                stale_note=staleness_of(signal).note,
            )
        )
    return drivers


def _confidence(
    signals: list[Signal],
    source_types: set[str],
    counts: dict[str, int],
    *,
    strength: float | None = None,
) -> float:
    """How much to trust the stance.

    Driven mainly by whether *independent kinds of source* agree. Ten findings
    extracted from one filing are one opinion about one document; the same
    conclusion reached from a filing, a call, and news coverage is three.

    The volume term is the informative strength, not the number of findings.
    Counting findings here reintroduced the artifact the genre correction exists
    to remove: a company that published twenty routine risk factors scored full
    marks for volume, and once thin verdicts began to be shown rather than
    withheld, that produced the visible contradiction of a brief captioned "this
    is a thin read" beside a confidence of 0.71 — higher than a company with half
    again as much real evidence. Confidence and stated thinness now move
    together because they are computed from the same quantity.
    """
    if not signals:
        return 0.0

    breadth = min(len(source_types) / 3.0, 1.0)          # 3+ kinds of source is full marks
    directional = counts["positive"] + counts["negative"]
    agreement = (
        max(counts["positive"], counts["negative"]) / directional if directional else 0.0
    )
    volume = (
        min(strength / _MIN_STRENGTH_FOR_STRONG, 1.0) if strength is not None
        else min(len(signals) / 8.0, 1.0)
    )
    mean_confidence = sum(s.confidence or 0.0 for s in signals) / len(signals)

    score = 0.40 * breadth + 0.25 * agreement + 0.15 * volume + 0.20 * mean_confidence
    return round(min(max(score, 0.0), 1.0), 3)


def _months_ago(days: int) -> str:
    """Plain words for an age. Nobody reads "247 days"."""
    if days < 45:
        return "the past few weeks"
    months = round(days / 30)
    if months < 12:
        return f"about {months} months ago"
    return "more than a year ago"


def _soften(stance: Stance) -> Stance:
    """Step a strong verdict down to its ordinary form."""
    if stance == Stance.STRONG_NEGATIVE:
        return Stance.NEGATIVE
    if stance == Stance.STRONG_POSITIVE:
        return Stance.POSITIVE
    return stance


def _stance_for(
    mean: float,
    directional_share: float,
    assessed_share: float,
    *,
    assessed_count: int = 0,
    source_count: int = 0,
    informative: float | None = None,
    count_informative: int | None = None,
) -> Stance:
    """The stance, with every refusal checked before any verdict.

    `informative` is how many genuinely informative observations the findings
    amount to, from engine/disclosure.py, and where it is supplied it replaces
    the raw count in both thinness tests. That substitution is the difference
    between "this company filed six findings" and "this company told Loom
    something six times": a record made entirely of the disclosures every
    annual report contains satisfies a count of six and carries the evidence
    of about one, and issuing a verdict off it is how the counting artifact
    reappears with its sign reversed.
    """
    # Unread is not the same as calm. Checked before anything else, because
    # every other branch assumes the evidence has actually been judged.
    if assessed_share < _MIN_ASSESSED_SHARE:
        return Stance.INSUFFICIENT
    if directional_share < _MIN_DIRECTIONAL_SHARE:
        return Stance.QUIET

    # A single finding cannot carry a verdict, however lopsided it looks. This
    # is the only remaining route to INSUFFICIENT from having read something,
    # and it is a count of findings that said something rather than a weighted
    # sum of how much they said.
    #
    # Abstention is meant to be what is left when there is nothing to report,
    # not the engine's preferred answer. The previous form of this test compared
    # a clustering-corrected, magnitude-weighted sum against a threshold written
    # for a plain count, which refused a verdict on 38 of 40 companies including
    # ones with a dozen informative findings across several filings. Thinness
    # now travels with the verdict instead of replacing it.
    if count_informative is not None:
        if count_informative < _MIN_INFORMATIVE_FINDINGS:
            return Stance.INSUFFICIENT
    elif (assessed_count if informative is None else informative) < _MIN_INFORMATIVE_FINDINGS:
        return Stance.INSUFFICIENT

    if mean <= -_STRONG:
        raw = Stance.STRONG_NEGATIVE
    elif mean <= -_LEAN:
        raw = Stance.NEGATIVE
    elif mean >= _STRONG:
        raw = Stance.STRONG_POSITIVE
    elif mean >= _LEAN:
        raw = Stance.POSITIVE
    else:
        raw = Stance.MIXED

    # Strength, not sufficiency, decides how firmly the lean may be stated.
    strength = assessed_count if informative is None else informative
    thin = strength < _MIN_STRENGTH_FOR_STRONG or source_count < _MIN_SOURCES_FOR_STRONG
    return _soften(raw) if thin else raw


def _plural(n: int, word: str) -> str:
    return f"{n} {word}" if n == 1 else f"{n} {word}s"


def _headline(
    stance: Stance,
    drivers: list[Driver],
    counts: dict[str, int],
    source_types: set[str],
    *,
    unassessed: int = 0,
    total: int = 0,
    adjusted: bool = False,
    routine: float | None = None,
    informative_n: int | None = None,
    strength: float | None = None,
    documents_read: int = 0,
) -> str:
    """One plain sentence. Built from structure, not borrowed from the model.

    The rule of thumb is that this must be readable by someone who does not
    know what a 10-Q is, so source kinds are named in ordinary words and no
    finding jargon is repeated verbatim.

    **Two different sentences, because the stance means two different things.**
    Unadjusted, it is an absolute reading and "more positives than concerns" is
    a true description of it. Genre-adjusted, it is a comparison against what
    documents of this kind normally carry, and the same phrasing becomes a lie:
    Microsoft holds 24 concerns against 13 positives and still reads better than
    an annual report normally does, because nineteen of those concerns are risk
    factors and every annual report's risk section is negative. Writing "more
    positives than concerns" over those counts would be contradicted by the very
    list printed underneath it, which is the failure the driver selection rules
    already exist to prevent. So the adjusted sentence states the comparison it
    is actually making, gives the raw counts anyway, and says how much of the
    record was routine.
    """
    if stance == Stance.INSUFFICIENT:
        if total and unassessed:
            # The distinction matters: this company has material, it just has
            # not been read yet, and telling the reader that is the honest move.
            return (
                f"{_plural(total, 'finding')} collected, but {unassessed} of the ones "
                f"that could carry a direction do not state one, so no view is offered."
            )
        if adjusted and total:
            # A third case, and the one the genre correction created. Loom did
            # read this company and did assess what it found; almost all of it
            # was the disclosure its documents are required to contain. Saying
            # "not enough analysed" would be false, and saying anything
            # directional would be reading a verdict off boilerplate.
            how_much = (
                f"about {round(routine * 100)}% of them are"
                if routine is not None and routine >= _ROUTINE_WORTH_SAYING
                else "nearly all of them are"
            )
            return (
                f"{_plural(total, 'finding')} read here, but {how_much} the disclosures "
                f"documents of this kind always contain, so there is not yet enough that "
                f"is specific to this company to form a view."
            )
        return "Not enough has been analysed yet to form a view."
    if stance == Stance.QUIET:
        return "Recent disclosures are routine, with nothing that changes the picture."

    if adjusted:
        return _adjusted_headline(
            stance, drivers, counts, source_types, routine,
            informative_n=informative_n, strength=strength,
            documents_read=documents_read,
        )

    named = [SOURCE_LABELS.get(s, s) for s in sorted(source_types)]
    if len(named) > 2:
        where = f"{', '.join(named[:-1])}, and {named[-1]}"
    else:
        where = " and ".join(named) if named else "recent disclosures"

    lead = drivers[0] if drivers else None
    # A label reads naturally mid-sentence ("led by component cost inflation");
    # a clipped sentence does not, and lowercasing one drags filing jargon
    # like "10-Q" into the plainest line on the screen.
    led_by = f", led by {lead.title.rstrip('.').lower()}," if lead and lead.is_label else ","

    if stance in (Stance.STRONG_NEGATIVE, Stance.NEGATIVE):
        strength = "Several serious concerns" if stance == Stance.STRONG_NEGATIVE else "More concerns than positives"
        return f"{strength}{led_by} showing up across {where}."
    if stance in (Stance.STRONG_POSITIVE, Stance.POSITIVE):
        strength = "Clearly encouraging" if stance == Stance.STRONG_POSITIVE else "More positives than concerns"
        return f"{strength}{led_by} across {where}."

    return (
        f"Evidence points both ways: {_plural(counts['negative'], 'concern')} "
        f"against {_plural(counts['positive'], 'positive')}, across {where}."
    )


def _adjusted_headline(
    stance: Stance,
    drivers: list[Driver],
    counts: dict[str, int],
    source_types: set[str],
    routine: float | None,
    *,
    informative_n: int | None = None,
    strength: float | None = None,
    documents_read: int = 0,
) -> str:
    """The sentence for a stance measured against its genre rather than zero."""
    named = [SOURCE_LABELS.get(s, s) for s in sorted(source_types)]
    if len(named) > 2:
        where = f"{', '.join(named[:-1])}, and {named[-1]}"
    else:
        where = " and ".join(named) if named else "recent disclosures"

    lead = drivers[0] if drivers else None
    led_by = f", led by {lead.title.rstrip('.').lower()}," if lead and lead.is_label else ","

    frame = {
        Stance.STRONG_NEGATIVE: "Reads considerably worse than companies of this kind usually do",
        Stance.NEGATIVE: "Reads worse than companies of this kind usually do",
        Stance.POSITIVE: "Reads better than companies of this kind usually do",
        Stance.STRONG_POSITIVE: "Reads clearly better than companies of this kind usually do",
        Stance.MIXED: "Reads about as companies of this kind usually do",
    }.get(stance, "")

    sentence = f"{frame}{led_by} across {where}."

    # Said out loud rather than left to the confidence number. Loom used to
    # withhold the lean entirely when the evidence was thin, which erased the
    # direction it had computed and made every thin case look like every other.
    # Stating the lean and its thinness in the same sentence is strictly more
    # information than a blank, and keeps the reader from mistaking a two-finding
    # read for a settled one.
    if strength is not None and strength < _MIN_STRENGTH_FOR_STRONG:
        detail = ""
        if informative_n:
            detail = f" only {_plural(informative_n, 'finding')} told Loom anything"
            if documents_read:
                detail += f", from {_plural(documents_read, 'document')}"
        sentence += f" This is a thin read:{detail or ' the evidence is limited'}."

    # The raw counts, always, and unrounded. A reader must be able to see the
    # tally the verdict was reached over, especially when the verdict points
    # the other way from the obvious reading of it.
    sentence += (
        f" {_plural(counts['negative'], 'concern')} against "
        f"{_plural(counts['positive'], 'positive')}"
    )
    if routine is not None and routine >= _ROUTINE_WORTH_SAYING:
        sentence += (
            f", of which about {round(routine * 100)}% is the disclosure documents of "
            f"this kind always contain."
        )
    else:
        sentence += "."
    return sentence


def _what_changed(signals: list[Signal], since: datetime | None) -> str | None:
    """Findings newer than the previous brief, described plainly."""
    if since is None:
        return None
    fresh = [s for s in signals if _aware(s.occurred_at) > _aware(since)]
    if not fresh:
        return None

    negative = sum(1 for s in fresh if direction_label(s) == "negative")
    positive = sum(1 for s in fresh if direction_label(s) == "positive")
    lead = max(fresh, key=lambda s: s.priority or 0.0)
    lead_title, _ = _title_of(lead)

    parts = [f"{_plural(len(fresh), 'new finding')} since the last read"]
    if negative or positive:
        parts.append(f"({negative} negative, {positive} positive)")
    return f"{' '.join(parts)}. Most significant: {lead_title.lower()}."


def build_brief(
    signals: list[Signal],
    *,
    previous_generated_at: datetime | None = None,
    now: datetime | None = None,
    horizon: str | None = None,
    norms: DisclosureNorms | None = None,
) -> Brief:
    """Fold one company's findings into a single read, for a stated holding period.

    `horizon` decides both which findings are still admissible and how much
    each counts. Omitting it keeps the original behaviour, a single undated
    window, which is what every stored brief was built with.

    `norms` is the measured expectation table from engine/disclosure.py. With
    it, the stance is how far this company's disclosure departs from what
    documents of the same kind normally say; without it, the stance is the
    plain average of finding directions, which scores a company down for the
    number of risk factors it publishes. Every production caller supplies one.
    It stays a parameter rather than being fetched here so the synthesis keeps
    its promise to hold no session and be testable without a database.
    """
    now = now or datetime.now(timezone.utc)
    spec = HORIZONS.get(horizon or "") if horizon else None
    window_days = spec.window_days if spec else WINDOW_DAYS
    cutoff = now - timedelta(days=window_days)

    live = [s for s in signals if s.dismissed_at is None]
    recent = [s for s in live if _aware(s.occurred_at) >= cutoff]
    substantive = [s for s in recent if s.signal_type in _SUBSTANTIVE_TYPES]

    # Nothing current, but the company may still have been read. Falling back
    # is not the same as widening the window: it only happens when the window
    # is empty, so a company under continuous coverage is never judged on stale
    # evidence while fresh evidence exists.
    fell_back = False
    if not substantive and spec is None:
        older_cutoff = now - timedelta(days=FALLBACK_WINDOW_DAYS)
        older = [
            s for s in live
            if _aware(s.occurred_at) >= older_cutoff and s.signal_type in _SUBSTANTIVE_TYPES
        ]
        if older:
            substantive = older
            recent = [s for s in live if _aware(s.occurred_at) >= older_cutoff]
            fell_back = True

    newest_age = (
        (now - max(_aware(s.occurred_at) for s in substantive)).days
        if substantive else None
    )

    # A short horizon over a thin recent record is the case most likely to
    # mislead: two stale findings can produce a confident one-week verdict that
    # nothing supports. Saying there is not enough to judge is the honest
    # answer and is itself useful.
    too_thin = spec is not None and len(substantive) < spec.min_findings

    if not substantive or too_thin:
        return Brief(
            stance=Stance.INSUFFICIENT,
            headline=(
                f"Not enough recent evidence to judge {spec.label}."
                if spec is not None
                else _headline(Stance.INSUFFICIENT, [], {"positive": 0, "negative": 0, "neutral": 0}, set())
            ),
            confidence=0.0,
            drivers=[],
            counterpoint=None,
            what_changed=None,
            source_types=[],
            signal_count=len(substantive),
            evidence={
                "window_days": window_days,
                "horizon": spec.key if spec else None,
                "findings_available": len(substantive),
                "findings_required": spec.min_findings if spec else None,
                "newest_finding_days": newest_age,
                "fell_back_to_older_evidence": fell_back,
            },
        )

    # Computed over what is actually being folded, not over the whole history:
    # a document whose findings were mostly cut by the window contributes the
    # findings that survived it, and should be weighted for those.
    parity = parity_weights(substantive)
    # Restatement is judged against everything Loom holds for this company,
    # including findings older than the window. That is the point: a risk
    # first disclosed two years ago and repeated since is not news now, and a
    # window that cannot see the original would call every copy the first one.
    restated_ids = restated(substantive, population=live)

    # Findings whose quoted period is years older than the filing that carried
    # them. Computed over the folded set rather than the whole history: this is
    # about what the current verdict rests on.
    stale = stale_ids(substantive)

    mean, _weight, counts, workings = _weighted_direction(
        substantive, spec, norms=norms, parity=parity, restated_ids=restated_ids,
        stale_ids=stale,
    )
    informative = (
        effective_findings(
            substantive, norms, parity=parity, restated_ids=restated_ids,
        )
        if norms is not None else None
    )
    # Two numbers because they answer two questions. The count decides whether
    # Loom may lean at all; the strength decides how firmly, and is what the
    # reader is shown so that a thin verdict is legible as thin rather than
    # indistinguishable from a confident one.
    informative_n = informative_count(substantive, norms) if norms is not None else None
    documents_read = len({
        str(getattr(s_, "source_document_id", None)) for s_ in substantive
        if getattr(s_, "source_document_id", None) is not None
    })
    # Two different questions, deliberately measured differently. `informative`
    # decides whether there is enough independent evidence to say anything and
    # therefore carries the clustering correction; `routine` is what the reader
    # is told about the company's disclosure and must not.
    routine = routine_share(substantive, norms) if norms is not None else None
    # How old each side of the case is, by the period its evidence describes.
    ages = side_ages(substantive, now=now)
    directional = counts["positive"] + counts["negative"]
    assessed = directional + counts["neutral"]
    directional_share = directional / len(substantive) if substantive else 0.0
    # Measured over the findings that *could* carry a direction, not over all of
    # them. A quote has no direction by nature, and counting it as an unjudged
    # finding would refuse a verdict on any company whose record contains
    # quotes — which is a third of the corpus.
    capable = [s_ for s_ in substantive if can_be_directional(s_)]
    assessed_share = (assessed / len(capable)) if capable else 0.0
    stance = _stance_for(
        mean, directional_share, assessed_share,
        assessed_count=assessed, source_count=len(source_types_of(substantive)),
        informative=informative, count_informative=informative_n,
    )

    source_types = source_types_of(substantive)

    # Drivers must argue for the stance, not against it.
    stance_direction = (
        "negative" if stance in (Stance.STRONG_NEGATIVE, Stance.NEGATIVE)
        else "positive" if stance in (Stance.STRONG_POSITIVE, Stance.STRONG_POSITIVE, Stance.POSITIVE)
        else None
    )
    drivers = _pick_drivers(substantive, stance_direction, norms=norms)
    counterpoint = _pick_counterpoint(substantive, stance_direction, norms=norms)
    # No view means no confidence in a view. Reporting "83% confident" beside
    # "no view is offered" reads as a contradiction and undermines both.
    confidence = (
        0.0 if stance == Stance.INSUFFICIENT
        else _confidence(substantive, source_types, counts, strength=informative)
    )

    headline = _headline(
        stance, drivers, counts, source_types,
        unassessed=len(substantive) - assessed, total=len(substantive),
        adjusted=norms is not None, routine=routine,
        informative_n=informative_n, strength=informative,
        documents_read=documents_read,
    )
    if fell_back and newest_age is not None:
        # Stated rather than implied. A reader deciding today is entitled to
        # know the verdict rests on a filing from several months ago, and an
        # analyst reading an old 10-K says so rather than discarding it.
        headline = (
            f"{headline} Based on filings from {_months_ago(newest_age)}, the most "
            f"recent Loom has read for this company."
        )

    return Brief(
        stance=stance,
        headline=headline,
        confidence=confidence,
        drivers=drivers,
        counterpoint=counterpoint,
        what_changed=_what_changed(substantive, previous_generated_at),
        source_types=sorted(source_types),
        signal_count=len(substantive),
        evidence={
            "window_days": window_days,
            "horizon": spec.key if spec else None,
            "horizon_label": spec.label if spec else None,
            "direction_mean": round(mean, 3),
            "counts": counts,
            "directional_share": round(directional_share, 3),
            "assessed_share": round(assessed_share, 3),
            "unassessed": len(substantive) - assessed,
            # How old the newest evidence is, and whether the window had to be
            # widened to find any. A verdict resting on an eight month old
            # annual report is legitimate and the reader is told.
            "newest_finding_days": newest_age,
            "fell_back_to_older_evidence": fell_back,
            # The workings behind the residual, so a stance can be audited
            # rather than taken on faith. `expected_mean` is what documents of
            # this composition normally carry; the stance is the gap between
            # that and what this company's actually did.
            "genre_adjusted": norms is not None,
            # How many of those findings actually told Loom something, as
            # distinct from how many there were. The gap between this and
            # `counts` is the measure of how much of a company's disclosure is
            # the boilerplate its genre requires.
            "informative_findings": round(informative, 2) if informative is not None else None,
            # How many findings said something, and how much independent
            # evidence that amounts to once clustering and repetition are
            # accounted for. Both are shown, because a lean resting on two
            # findings from one filing and a lean resting on eight across four
            # filings are not the same claim and must not render alike.
            "informative_count": informative_n,
            "evidence_strength": round(informative, 2) if informative is not None else None,
            "strength_for_strong": _MIN_STRENGTH_FOR_STRONG,
            "minimum_informative": _MIN_INFORMATIVE_FINDINGS,
            "documents_read": documents_read,
            # The same quantity as a share, which is the form the interface and
            # the digest both want.
            "routine_share": round(routine, 3) if routine is not None else None,
            "expected_mean": workings["expected_mean"],
            # The quantity the stance is actually computed from, and the
            # per-document arithmetic behind it, so a verdict can be checked
            # against the filings rather than taken on faith.
            "incidence_residual": workings.get("incidence_residual"),
            "incidence": workings.get("incidence"),
            "excess_mean": workings["raw_excess"],
            "restated_findings": workings["restated"],
            # How many findings said nothing Loom did not already expect from a
            # document of that kind, and were therefore left out of the stance.
            "uninformative_findings": workings["uninformative"],
            # Evidence that is older than its timestamp suggests, and whether
            # the two sides of the case are the same age. Both are reported even
            # when there is nothing to report, so a reader can tell "Loom
            # checked and found none" from "Loom did not check".
            "stale_findings": workings["stale"],
            "evidence_age_note": ages.note,
            "positive_age_days": ages.positive_days,
            "negative_age_days": ages.negative_days,
            "documents": len({document_key(s) for s in substantive}),
        },
    )
