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
from app.engine.statistics.engine import ANOMALY_RATE
from app.engine.statistics.features import MAGNITUDE_WEIGHT
from app.models.signal import Signal, SignalType

# Bumped when the synthesis rules change, so stored briefs can be regenerated
# deliberately rather than drifting silently.
ENGINE_VERSION = "2026-08-26.1"

WINDOW_DAYS = 90

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
_MIN_FOR_ANY_VERDICT = 2
_MIN_FINDINGS_FOR_STRONG = 3
_MIN_SOURCES_FOR_STRONG = 2

# Two findings above this token overlap are the same story told twice. Without
# this, one theme that appears in a filing, a call, and three news items fills
# every driver slot and hides everything else.
_DUPLICATE_OVERLAP = 0.4

MAX_DRIVERS = 3

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

STANCE_LABELS = {
    Stance.STRONG_NEGATIVE: "Serious concerns",
    Stance.NEGATIVE: "Leaning negative",
    Stance.MIXED: "Mixed picture",
    Stance.POSITIVE: "Leaning positive",
    Stance.STRONG_POSITIVE: "Clearly positive",
    Stance.QUIET: "Nothing notable",
    Stance.INSUFFICIENT: "Not enough data yet",
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
    signals: list[Signal], horizon: Horizon | None = None
) -> tuple[float, float, dict[str, int]]:
    """Return (mean_direction, total_weight, counts). Mean is -1..1."""
    counts = {"positive": 0, "negative": 0, "neutral": 0}
    total = 0.0
    weighted = 0.0
    for s in signals:
        direction = s.market_direction
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
        sign = 1.0 if direction == "positive" else -1.0 if direction == "negative" else 0.0
        weighted += weight * sign
        total += weight
    mean = weighted / total if total else 0.0
    return mean, total, counts


def _pick_counterpoint(signals: list[Signal], stance_direction: str | None) -> Driver | None:
    """The highest-priority finding that argues against the stance."""
    if stance_direction not in ("positive", "negative"):
        return None
    opposite = "negative" if stance_direction == "positive" else "positive"
    against = [s for s in signals if s.market_direction == opposite]
    if not against:
        return None
    best = max(against, key=lambda s: s.priority or 0.0)
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


def _pick_drivers(signals: list[Signal], stance_direction: str | None = None) -> list[Driver]:
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
        matching = [s for s in signals if s.market_direction == stance_direction]
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
    ordered = sorted(
        aligned,
        key=lambda s: (is_unusual_for_company(s), s.priority or 0.0),
        reverse=True,
    )
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
                direction=signal.market_direction or "unassessed",
                magnitude=signal.market_magnitude or "moderate",
                sources=sorted(sources),
                signal_ids=[str(signal.id), *[str(s.id) for s in supporting]],
                evidence_rate=signal.evidence_rate,
                evidence_sample_size=signal.evidence_sample_size,
            )
        )
    return drivers


def _confidence(signals: list[Signal], source_types: set[str], counts: dict[str, int]) -> float:
    """How much to trust the stance.

    Driven mainly by whether *independent kinds of source* agree. Ten findings
    extracted from one filing are one opinion about one document; the same
    conclusion reached from a filing, a call, and news coverage is three.
    """
    if not signals:
        return 0.0

    breadth = min(len(source_types) / 3.0, 1.0)          # 3+ kinds of source is full marks
    directional = counts["positive"] + counts["negative"]
    agreement = (
        max(counts["positive"], counts["negative"]) / directional if directional else 0.0
    )
    volume = min(len(signals) / 8.0, 1.0)
    mean_confidence = sum(s.confidence or 0.0 for s in signals) / len(signals)

    score = 0.40 * breadth + 0.25 * agreement + 0.15 * volume + 0.20 * mean_confidence
    return round(min(max(score, 0.0), 1.0), 3)


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
) -> Stance:
    # Unread is not the same as calm. Checked before anything else, because
    # every other branch assumes the evidence has actually been judged.
    if assessed_share < _MIN_ASSESSED_SHARE:
        return Stance.INSUFFICIENT
    if directional_share < _MIN_DIRECTIONAL_SHARE:
        return Stance.QUIET

    # A single finding cannot carry a verdict, however lopsided it looks.
    if assessed_count < _MIN_FOR_ANY_VERDICT:
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

    thin = assessed_count < _MIN_FINDINGS_FOR_STRONG or source_count < _MIN_SOURCES_FOR_STRONG
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
) -> str:
    """One plain sentence. Built from structure, not borrowed from the model.

    The rule of thumb is that this must be readable by someone who does not
    know what a 10-Q is, so source kinds are named in ordinary words and no
    finding jargon is repeated verbatim.
    """
    if stance == Stance.INSUFFICIENT:
        if total and unassessed:
            # The distinction matters: this company has material, it just has
            # not been read yet, and telling the reader that is the honest move.
            return (
                f"{_plural(total, 'finding')} collected, but {unassessed} have not been "
                f"assessed for market impact yet, so no view is offered."
            )
        return "Not enough has been analysed yet to form a view."
    if stance == Stance.QUIET:
        return "Recent disclosures are routine, with nothing that changes the picture."

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


def _what_changed(signals: list[Signal], since: datetime | None) -> str | None:
    """Findings newer than the previous brief, described plainly."""
    if since is None:
        return None
    fresh = [s for s in signals if _aware(s.occurred_at) > _aware(since)]
    if not fresh:
        return None

    negative = sum(1 for s in fresh if s.market_direction == "negative")
    positive = sum(1 for s in fresh if s.market_direction == "positive")
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
) -> Brief:
    """Fold one company's findings into a single read, for a stated holding period.

    `horizon` decides both which findings are still admissible and how much
    each counts. Omitting it keeps the original behaviour, a single undated
    window, which is what every stored brief was built with.
    """
    now = now or datetime.now(timezone.utc)
    spec = HORIZONS.get(horizon or "") if horizon else None
    window_days = spec.window_days if spec else WINDOW_DAYS
    cutoff = now - timedelta(days=window_days)

    recent = [
        s for s in signals
        if _aware(s.occurred_at) >= cutoff and s.dismissed_at is None
    ]
    substantive = [s for s in recent if s.signal_type in _SUBSTANTIVE_TYPES]

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
            },
        )

    mean, _weight, counts = _weighted_direction(substantive, spec)
    directional = counts["positive"] + counts["negative"]
    assessed = directional + counts["neutral"]
    directional_share = directional / len(substantive) if substantive else 0.0
    assessed_share = assessed / len(substantive) if substantive else 0.0
    stance = _stance_for(
        mean, directional_share, assessed_share,
        assessed_count=assessed, source_count=len(source_types_of(substantive)),
    )

    source_types = source_types_of(substantive)

    # Drivers must argue for the stance, not against it.
    stance_direction = (
        "negative" if stance in (Stance.STRONG_NEGATIVE, Stance.NEGATIVE)
        else "positive" if stance in (Stance.STRONG_POSITIVE, Stance.STRONG_POSITIVE, Stance.POSITIVE)
        else None
    )
    drivers = _pick_drivers(substantive, stance_direction)
    counterpoint = _pick_counterpoint(substantive, stance_direction)
    # No view means no confidence in a view. Reporting "83% confident" beside
    # "no view is offered" reads as a contradiction and undermines both.
    confidence = (
        0.0 if stance == Stance.INSUFFICIENT
        else _confidence(substantive, source_types, counts)
    )

    return Brief(
        stance=stance,
        headline=_headline(
            stance, drivers, counts, source_types,
            unassessed=len(substantive) - assessed, total=len(substantive),
        ),
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
        },
    )
