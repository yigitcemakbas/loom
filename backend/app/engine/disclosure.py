"""What a document of this kind normally says, so Loom can tell it apart from
what this company is actually saying.

This module exists because of a measurement. Across Loom's whole corpus, the
finding type `new_risk_factor` is assessed negative **158 times out of 158**.
Not predominantly negative: exclusively. A risk factor cannot be positive,
because a risk factor is a description of something that could go wrong, and
every annual report in the world contains a section full of them. The same
holds more softly elsewhere: a `notable_quote` pulled from a 10-K runs 34
negative to 11 positive, while the identical finding type pulled from an
earnings call runs 3 negative to 13 positive. Same extraction, same prompt,
opposite distribution, decided entirely by which kind of document it came from.

The brief was averaging those directions. That arithmetic has an unavoidable
consequence: a company is scored down for the number of risk factors it
discloses, which is a fact about how much it publishes rather than about how
it is doing. In the stored corpus, six companies read as seriously troubled on
the strength of a single annual report each, and Micron reads at a direction
mean of exactly -1.0 from fourteen risk factors and nothing else. Loom was not
measuring those companies. It was measuring the genre of the one document it
had read.

**The correction is to score the residual, not the level.** A finding's
contribution is how far its direction sits from what a finding of that type,
from that kind of document, normally carries. A negative risk factor in an
annual report is exactly what an annual report contains, so it contributes
nothing. A negative *guidance change* is not, so it contributes almost its
full weight. Nothing is discarded and nothing is reweighted by hand: the
expectation is measured from what Loom has actually read.

Three mechanisms, in the order they matter.

**Genre expectation.** The residual above. It is the one that fixes the
headline artifact.

**Company norms, where the history supports them.** The expectation is
hierarchical: a finding is measured against its own company's habit first,
that company's habit is shrunk toward the genre's, and the genre's toward the
signal type's. A company that has always written a bleak risk section has to
write a bleaker one than usual before Loom calls it negative; a company that
has never done so is judged by the corpus until it has a habit of its own.
This is deliberately not a single universal table. Two companies filing the
same words are not saying the same thing, and the crossover between "judged by
the corpus" and "judged by itself" is smooth rather than a threshold.

**Restatement.** A risk that appeared in last year's filing and appears again
in this one is not new information; it is the same sentence, filed again. The
overlap test is the same one the brief already uses to stop one story filling
every driver slot, applied across time instead of within a window.

**Document parity.** Forty findings from one annual report are not forty
independent observations, they are one document read closely. A document's
combined weight grows as the square root of its finding count rather than
linearly, which is the shape any clustered sample takes: it stops a company
that publishes more from outvoting one that publishes less, without pretending
that a thoroughly read filing says no more than a thinly read one.

Everything here is deterministic and auditable. No model call, and every
number a caller sees carries the counts it was measured from.
"""

import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from math import sqrt
from typing import Iterable, Optional

from app.engine.statistics.statistics import shrunk_mean

# There is deliberately no rescaling constant here, and an earlier version of
# this module had one. Once findings that said nothing are excluded from the
# stance rather than diluting it, the residual mean is already the quantity the
# brief's thresholds are written against: it runs from -1, meaning every
# informative finding was a complete reversal of expectation in the negative
# direction, through 0 at balance, to +1. A scaling factor on top of that made
# every well-covered company saturate at the strongest reading available, which
# is how it was found.

# Pseudo-observations behind each level of the hierarchy. At this weight a
# company needs roughly eight of its own findings of a given kind before its
# habit outweighs the genre's, which is about two filings' worth.
NORM_PRIOR_WEIGHT = 8.0

# Observations a sector needs in one (genre, type) cell before it gets a norm
# of its own rather than falling through to the genre's.
#
# Shrinkage already stops a thin sample producing a confident number, but a
# floor stops the engine claiming to describe an industry it has barely seen:
# seventeen findings from two utilities is two companies' habits wearing a
# sector's name. The same argument, and roughly the same number, as
# MIN_SECTOR_MEMBERS in the factor library.
MIN_SECTOR_OBSERVATIONS = 10

# Two findings above this token overlap are the same disclosure filed twice.
#
# Measured rather than guessed, and the first value was wrong. Set at 0.5 on the
# reasoning that a stricter bar than the brief's own de-duplication threshold
# was safer, it never fired once: across the stored corpus the highest overlap
# between two same-type findings from different documents is 0.42, because
# Loom's summaries are its own one-line sentences rather than the filing's
# words, and two writings of the same idea do not share half their vocabulary.
# At 0.30 the detector finds the real cases, which on inspection are the same
# risk carried across filings: three separate writings of Apple's memory cost
# pressure, Nvidia's open-source displacement risk in two annual reports.
RECURRENCE_OVERLAP = 0.30

# Figures a restatement may not introduce. Two findings can share almost all
# their wording and still be different information, and the tokenizer above
# cannot see the difference because it discards anything shorter than four
# letters and every digit. So AMD guiding to $11.2bn in one quarter and $13bn
# in the next scored the same overlap as the same risk written twice, and
# discounting the second would have thrown away the most current number Loom
# holds.
#
# The rule is asymmetric on purpose: a later finding that names a figure the
# earlier one did not is an update, whatever words it reuses. A later finding
# that names nothing new is a restatement. Where this is unsure it declines to
# call a restatement, which costs a discount rather than misapplying one.
_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")

# What a restated disclosure is still worth. Not zero: a company continuing to
# carry a risk is weak evidence that the risk is still live, and dropping it
# entirely would let a company bury a deteriorating situation by describing it
# in the same words every year.
RECURRENCE_DISCOUNT = 0.25

# Below this residual, a finding is treated as having said nothing, and is
# excluded from the stance entirely rather than counted as a weak vote.
#
# Excluding it from the *denominator* is the part that matters, and it took a
# test to notice. A risk factor contributes approximately nothing to the
# numerator either way, but leaving it in the denominator means a company with
# forty of them has its informative findings diluted eight times more than a
# company with six, and the reading weakens in proportion to how much the
# company published. That is the original artifact in a milder form: quieter,
# still driven by volume rather than by content.
#
# So an uninformative finding neither votes nor dilutes. Whether enough
# informative evidence remains to say anything at all is a separate question,
# asked separately, by `effective_findings`.
UNINFORMATIVE_FLOOR = 0.10

GENRE_UNKNOWN = "unknown"

# Finding types that are never restatements by construction. A guidance change
# is a change; a quarter-over-quarter anomaly is defined against the previous
# quarter. Both say something new every time they occur however much wording
# they share with the last one, and the figure test alone would not always
# catch it.
_ALWAYS_NEW = frozenset({"guidance_change", "qoq_anomaly"})

_STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "into", "could", "may",
    "would", "will", "have", "has", "are", "was", "were", "its", "their", "which",
    "company", "companys", "risk", "risks", "increase", "increased", "increasing",
    "reduce", "reduced", "reducing", "impact", "material", "materially", "adverse",
    "adversely", "affect", "affected", "significant", "significantly", "continue",
    "continued", "results", "operations", "financial", "condition", "business",
}

_SIGN = {"positive": 1.0, "negative": -1.0, "neutral": 0.0}


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z]{4,}", (text or "").lower())
    return {w for w in words if w not in _STOPWORDS}


def _figures(text: str) -> set[str]:
    """Every number named in a piece of text, normalised.

    Kept separate from `_tokens`, which deliberately discards digits so that
    two writings of the same idea can be compared on their words. A figure is
    what distinguishes an update from a repetition, so it needs its own reading.
    """
    return {
        match.group().replace(",", "").rstrip(".0") or "0"
        for match in _NUMBER.finditer(text or "")
    }


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def genre_of(signal) -> str:
    """Which kind of document a finding came from.

    The genre is the unit the expectation is measured over, because it is what
    actually decides the distribution: an annual report's risk section reads
    one way and an earnings call reads another, whoever filed them.
    """
    subtype = (getattr(signal, "signal_metadata", None) or {}).get("doc_subtype")
    return subtype or GENRE_UNKNOWN


def type_of(signal) -> str:
    signal_type = getattr(signal, "signal_type", None)
    return getattr(signal_type, "value", signal_type) or GENRE_UNKNOWN


def sign_of(signal) -> Optional[float]:
    """+1, -1, or None when the document stated no direction.

    Delegates to engine/direction.py, which derives the sign from what the
    filing did. It used to read `market_direction`, a model's expectation of how
    the market would react — which made every stance a price forecast. See that
    module for the measurements that forced the change.

    None rather than 0.0, for the reason the whole codebase keeps repeating:
    an unassessed finding is not a balanced one, and a caller that wants to
    treat it as neutral must do so knowingly.
    """
    from app.engine.direction import documentary_sign

    return documentary_sign(signal)


def document_key(signal) -> str:
    """Which document a finding belongs to, for the parity correction.

    Falls back to the genre and date when a finding carries no document id,
    which is the case for findings synthesised across several disclosures and
    for those derived from structured facts rather than text. Grouping those
    by the day they describe is the right unit anyway: two insider findings
    from the same day's filings are one observation about one day.
    """
    document_id = getattr(signal, "source_document_id", None)
    if document_id is not None:
        return str(document_id)
    occurred = getattr(signal, "occurred_at", None)
    day = _aware(occurred).date().isoformat() if occurred else "undated"
    return f"{genre_of(signal)}@{day}"


@dataclass(frozen=True)
class Norm:
    """What findings of one kind normally carry, and how much data says so."""

    expected: float
    sample_size: int


@dataclass
class DisclosureNorms:
    """The measured expectation table, at every level of the hierarchy.

    Built once per run and handed to the brief. Keeping it a value rather than
    a database lookup means the synthesis stays pure: the same signals and the
    same norms always produce the same brief, which is what lets the ordering
    be tested without a session.
    """

    by_type: dict[str, Norm] = field(default_factory=dict)
    by_genre: dict[tuple[str, str], Norm] = field(default_factory=dict)
    # (sector, genre, signal_type). The level this table was missing.
    #
    # Loom learned once already that ranking a bank against a software company
    # is not a hard comparison but a meaningless one: its first universe-wide
    # factor run returned eight banks as the eight weakest companies in the
    # database, in order, having read nothing about any of them. The factor
    # library was rebuilt around sector peer groups because of it.
    #
    # These norms made exactly the same mistake in a different currency. With
    # no sector level, "what an annual report normally says" was measured over
    # a corpus that is 52% technology, and every utility, bank and retailer was
    # scored against it. Same error, same fix.
    by_sector: dict[tuple[str, str, str], Norm] = field(default_factory=dict)
    by_company: dict[tuple[str, str, str], Norm] = field(default_factory=dict)
    # Which sector each company belongs to, carried so a caller that has only
    # a signal can still reach the sector level. Built with the table rather
    # than looked up per call, because the table is the thing that knows which
    # sectors it actually measured.
    sector_of: dict[str, str] = field(default_factory=dict)

    # The expectation everything is ultimately shrunk toward. Zero: with
    # nothing measured, assuming disclosure leans neither way is the weakest
    # claim available, and any other value would be an opinion smuggled in as
    # a default.
    root: float = 0.0

    # How much directional disclosure a document of each genre normally carries.
    # Measured over the whole corpus and carried here rather than passed
    # separately, because every caller that has the norms needs it and a
    # company's own findings cannot be used to measure the norm it is judged
    # against — doing that makes the residual zero by construction.
    incidence: Optional["IncidenceNorms"] = None

    def expected_for(self, signal, sector: Optional[str] = None) -> Norm:
        """What a finding like this one normally carries.

        Resolved most specific first: this company, then its industry, then the
        kind of document, then the kind of finding. Each level was already
        shrunk toward the one above it when the table was built, so reading the
        deepest available entry is reading the whole hierarchy rather than just
        its last rung.
        """
        signal_type = type_of(signal)
        genre = genre_of(signal)
        company_id = getattr(signal, "company_id", None)
        if company_id is not None:
            found = self.by_company.get((str(company_id), genre, signal_type))
            if found is not None:
                return found
        sector = sector or self.sector_of.get(str(company_id)) if company_id else sector
        if sector:
            found = self.by_sector.get((sector, genre, signal_type))
            if found is not None:
                return found
        found = self.by_genre.get((genre, signal_type))
        if found is not None:
            return found
        found = self.by_type.get(signal_type)
        if found is not None:
            return found
        return Norm(expected=self.root, sample_size=0)

    def excess_for(self, signal) -> Optional[float]:
        """How far this finding departs from what its kind normally carries.

        None when the finding was never assessed, which the caller must handle
        rather than receive as zero. A negative risk factor in an annual
        report returns approximately 0.0, and that is the entire point of this
        module: it is not evidence of anything, because it is what annual
        reports contain.

        **Clamped to one step.** The raw residual is unbounded above by the
        genre's own gloom: where the expectation is -0.996, a positive finding
        has a residual of +1.996, and it would then count for twice as much as
        any finding can possibly be worth. Measured on the stored corpus, that
        is not a hypothetical. Uncapped, four concerns and two positives from a
        single annual report came out "leaning positive", which is the original
        artifact inverted rather than fixed. A finding may be worth a complete
        reversal of expectation and no more.
        """
        sign = sign_of(signal)
        if sign is None:
            return None
        excess = sign - self.expected_for(signal).expected
        return max(-1.0, min(1.0, excess))


def measure_norms(
    signals: Iterable, sectors: Optional[dict] = None
) -> DisclosureNorms:
    """Measure the expectation table from findings. Pure: no database.

    Four rungs, each shrunk toward the one above it, so a thin cell inherits
    its parent's answer smoothly rather than at a threshold:

        this company  ->  its sector  ->  the document genre  ->  the finding type

    `sectors` maps a company id to its industry. Without it the sector rung is
    simply absent and the table behaves as it did before, which is the honest
    degradation: a caller that cannot say which industry a company is in should
    not have one guessed for it.

    Only *directional* findings are measured. A finding assessed as neutral is
    a judgement that it does not point either way, and folding those into the
    expectation would drag every genre toward zero in proportion to how often
    the extraction declined to call one, which is a property of the prompt
    rather than of the documents.
    """
    sectors = {str(k): v for k, v in (sectors or {}).items() if v}
    # Materialised because the incidence pass reads the same findings again and
    # the parameter is an Iterable, which a generator would exhaust.
    signals = list(signals)

    per_type: dict[str, list[float]] = defaultdict(list)
    per_genre: dict[tuple[str, str], list[float]] = defaultdict(list)
    per_sector: dict[tuple[str, str, str], list[float]] = defaultdict(list)
    per_company: dict[tuple[str, str, str], list[float]] = defaultdict(list)

    for signal in signals:
        sign = sign_of(signal)
        if sign is None or sign == 0.0:
            continue
        signal_type = type_of(signal)
        genre = genre_of(signal)
        company_id = getattr(signal, "company_id", None)
        per_type[signal_type].append(sign)
        per_genre[(genre, signal_type)].append(sign)
        if company_id is not None:
            per_company[(str(company_id), genre, signal_type)].append(sign)
            sector = sectors.get(str(company_id))
            if sector:
                per_sector[(sector, genre, signal_type)].append(sign)

    norms = DisclosureNorms(sector_of=dict(sectors))
    norms.incidence = measure_incidence(signals, sectors)

    for signal_type, values in per_type.items():
        norms.by_type[signal_type] = Norm(
            expected=shrunk_mean(values, norms.root, NORM_PRIOR_WEIGHT),
            sample_size=len(values),
        )

    for (genre, signal_type), values in per_genre.items():
        parent = norms.by_type.get(signal_type)
        norms.by_genre[(genre, signal_type)] = Norm(
            expected=shrunk_mean(
                values, parent.expected if parent else norms.root, NORM_PRIOR_WEIGHT
            ),
            sample_size=len(values),
        )

    for (sector, genre, signal_type), values in per_sector.items():
        # Below the floor the sector gets no entry at all, so a lookup falls
        # through to the genre. Storing a shrunk value from four observations
        # would be technically defensible and practically a lie: it reads as a
        # statement about an industry and is a statement about two companies.
        if len(values) < MIN_SECTOR_OBSERVATIONS:
            continue
        parent = norms.by_genre.get((genre, signal_type)) or norms.by_type.get(signal_type)
        norms.by_sector[(sector, genre, signal_type)] = Norm(
            expected=shrunk_mean(
                values, parent.expected if parent else norms.root, NORM_PRIOR_WEIGHT
            ),
            sample_size=len(values),
        )

    for (company_id, genre, signal_type), values in per_company.items():
        # A company shrinks toward its own industry where Loom has measured
        # one, and toward the genre otherwise. This is the rung that carries
        # the correction: without it a utility's disclosure was pulled toward
        # a benchmark that is half technology.
        sector = sectors.get(company_id)
        parent = (
            (norms.by_sector.get((sector, genre, signal_type)) if sector else None)
            or norms.by_genre.get((genre, signal_type))
            or norms.by_type.get(signal_type)
        )
        norms.by_company[(company_id, genre, signal_type)] = Norm(
            expected=shrunk_mean(
                values, parent.expected if parent else norms.root, NORM_PRIOR_WEIGHT
            ),
            sample_size=len(values),
        )

    return norms


def parity_weights(signals: list) -> dict[str, float]:
    """How much each finding counts, given how many came from its document.

    A document holding n findings carries sqrt(n) units of voice rather than
    n, so each of its findings is worth 1/sqrt(n). One finding from a filing
    still counts for one; forty count for about six between them rather than
    forty.

    The square root is the shape a clustered sample takes, and it is used here
    for the property rather than for any claim about the correlation between
    findings in one document: it is monotone, so a closely read filing outvotes
    a thin one, and sublinear, so publishing more cannot by itself decide a
    verdict.
    """
    counts: dict[str, int] = defaultdict(int)
    for signal in signals:
        counts[document_key(signal)] += 1
    return {
        str(getattr(signal, "id", id(signal))): 1.0 / sqrt(counts[document_key(signal)])
        for signal in signals
    }


def restated(signals: list, *, population: Optional[list] = None) -> set[str]:
    """Findings that repeat a disclosure the company already made.

    A finding is restated when an *earlier* finding from a *different*
    document, of the same type, says substantially the same thing. Both
    conditions matter: the same words in the same filing are one finding seen
    twice and are handled by de-duplication, while the same words a year apart
    are a company carrying a risk forward.

    Returns the ids of the later copies only. The original stands at full
    weight, because the first time a company discloses something it is news.
    """
    population = population if population is not None else signals
    prior: list[tuple[datetime, str, str, set[str], set[str]]] = []
    for signal in population:
        occurred = getattr(signal, "occurred_at", None)
        if occurred is None:
            continue
        summary = getattr(signal, "summary", "") or ""
        prior.append((
            _aware(occurred),
            document_key(signal),
            type_of(signal),
            _tokens(summary),
            _figures(summary),
        ))

    found: set[str] = set()
    for signal in signals:
        occurred = getattr(signal, "occurred_at", None)
        if occurred is None:
            continue
        when = _aware(occurred)
        document = document_key(signal)
        signal_type = type_of(signal)
        if signal_type in _ALWAYS_NEW:
            continue
        summary = getattr(signal, "summary", "") or ""
        tokens = _tokens(summary)
        if not tokens:
            continue
        figures = _figures(summary)
        for other_when, other_document, other_type, other_tokens, other_figures in prior:
            if other_when >= when or other_document == document or other_type != signal_type:
                continue
            if _overlap(tokens, other_tokens) < RECURRENCE_OVERLAP:
                continue
            if figures - other_figures:
                # Names a figure the earlier finding did not. That is an update
                # wearing the same words, and discounting it would throw away
                # the most current number Loom holds about the company.
                continue
            found.add(str(getattr(signal, "id", id(signal))))
            break
    return found


def effective_findings(
    signals: list,
    norms: "DisclosureNorms",
    *,
    parity: Optional[dict[str, float]] = None,
    restated_ids: Optional[set[str]] = None,
) -> float:
    """How many genuinely informative observations a set of findings amounts to.

    This is the guard that keeps the correction from inverting the problem it
    fixes. The residual gets the *direction* right, but a company whose entire
    record is one annual report's risk section has a residual computed from
    almost nothing: the concerns contribute approximately zero because they are
    what annual reports contain, so the verdict ends up resting on whichever
    handful of findings happened to be informative. Measured on the stored
    corpus, that turned six companies from wrongly negative into wrongly
    positive, on two or three findings each.

    The honest answer for those companies is neither. It is that Loom has read
    them and has not yet learned anything, which is a thing Loom is supposed to
    be willing to say.

    So informativeness is counted, not findings. Each finding is worth its own
    residual, clustered by document (forty findings from one filing are not
    forty independent observations) and discounted where it restates an earlier
    disclosure. Magnitude and priority are deliberately left out: those measure
    how much a finding matters, and this measures whether it is evidence at all.
    """
    total = 0.0
    for signal in signals:
        excess = norms.excess_for(signal)
        if excess is None or sign_of(signal) == 0.0:
            continue
        if abs(excess) < UNINFORMATIVE_FLOOR:
            continue
        # One unit per finding that cleared the floor, adjusted only for
        # independence. Not `abs(excess)`.
        #
        # Weighting by the magnitude here charged every finding twice for the
        # same sin: the floor above has already removed the findings that said
        # nothing, and multiplying the survivors by their own residual then
        # discounted them again for not saying *enough*. Compounded with the
        # clustering correction it put the median company at 0.8 against a
        # threshold of 2, so thirty-eight of forty companies could not reach any
        # verdict and the engine returned "insufficient" 97% of the time. That
        # is not calibrated abstention, it is a constant.
        #
        # It also contradicted the paragraph above: magnitude is deliberately
        # excluded from this measure, because this one asks whether a finding is
        # evidence at all and magnitude answers how much it matters.
        weight = 1.0
        if parity is not None:
            weight *= parity.get(str(getattr(signal, "id", id(signal))), 1.0)
        if restated_ids and str(getattr(signal, "id", id(signal))) in restated_ids:
            weight *= RECURRENCE_DISCOUNT
        total += weight
    return total


def informative_count(signals: list, norms: "DisclosureNorms") -> int:
    """How many findings told Loom something, counted plainly.

    The companion to `effective_findings` and deliberately not the same number.
    This one decides whether a verdict may be offered at all, and the rule it
    encodes is the original one: a single finding cannot carry a verdict, so two
    independent things must have said something. It is a raw count because that
    is the unit the rule was written in.

    `effective_findings` answers the other question, how much evidence there is
    once clustering and repetition are accounted for, and that continuous number
    decides how strongly the verdict may be phrased rather than whether it
    exists. Conflating the two is what made the gate 3 to 7 times stricter than
    it was ever meant to be.
    """
    n = 0
    for signal in signals:
        # A finding is evidence when the document stated a direction for it. The
        # per-finding excess floor that used to gate this is gone; see the note
        # in `effective_findings` for why it became vacuous.
        if sign_of(signal) is None:
            continue
        n += 1
    return n


def routine_share(signals: list, norms: "DisclosureNorms") -> Optional[float]:
    """What share of a company's assessed findings said nothing unexpected.

    Deliberately measured from the residuals alone, with no document parity and
    no restatement discount. Those two belong in `effective_findings`, where the
    question is how much independent evidence exists; this is the number a
    reader is shown, and the question it answers is narrower: of the things this
    company disclosed, how much of it is what its documents always say.

    Folding the clustering correction in here made the reported share read 83%
    for companies with a deep and genuinely informative record, because a
    fourteen-document company divides every finding by the square root of its
    document's count. True of the evidence weighting, meaningless as a
    description of the disclosure, and it would have told a reader that Apple's
    filings are almost entirely boilerplate when they are not.
    """
    # Measured from the incidence residual rather than from per-finding
    # residuals, which are now zero by construction and would have reported every
    # company as 100% boilerplate. The question is unchanged — how much of what
    # this company disclosed is what its documents always contain — and the
    # honest answer is how close its volume of disclosure sat to its genre's.
    sector = None
    for signal in signals:
        company = getattr(signal, "company_id", None)
        if company is not None:
            sector = getattr(norms, "sector_of", {}).get(str(company))
            break
    incidence = getattr(norms, "incidence", None)
    if incidence is None:
        return None
    residual, _workings = incidence_excess(signals, incidence, sector=sector)
    if residual is None:
        return None
    return max(0.0, min(1.0, 1.0 - abs(residual)))


__all__ = [
    "GENRE_UNKNOWN",
    "MIN_SECTOR_OBSERVATIONS",
    "NORM_PRIOR_WEIGHT",
    "RECURRENCE_DISCOUNT",
    "RECURRENCE_OVERLAP",
    "UNINFORMATIVE_FLOOR",
    "DisclosureNorms",
    "Norm",
    "document_key",
    "effective_findings",
    "informative_count",
    "Incidence",
    "IncidenceNorms",
    "measure_incidence",
    "incidence_excess",
    "genre_of",
    "measure_norms",
    "parity_weights",
    "routine_share",
    "restated",
    "sign_of",
    "type_of",
]


# --------------------------------------------------------------- incidence
#
# The residual moved here, and the reason is worth recording because it was only
# visible after direction became documentary.
#
# The original design measured the residual per finding: how far this finding's
# direction departs from what findings of its genre normally carry. That works
# when direction is a judgement that varies inside a type, which is what
# `market_direction` was. It collapses the moment direction becomes a property of
# the type: a new risk factor is always negative, so the norm for new risk
# factors is exactly negative, and the gap is identically zero. Measured on the
# stored corpus after the change, the mean absolute excess for a new risk factor
# was 0.0018 and not one of 195 cleared the informativeness floor.
#
# So the quantity that carries the signal is not which way a finding points. It
# is *how much of that kind of disclosure this company produced, against how much
# companies like it produce*. A 10-K that added fourteen new risk factors where
# its sector normally adds four is saying something. One that added four is not.
#
# That is documentary, it is checkable against two filings, it varies, and it is
# bidirectional without needing a resolved-risk-factor signal: fewer concerns
# than the genre expects reads better, which is the positive channel the
# one-directional diff cannot otherwise provide.

# Findings per document beyond which a count is treated as fully surprising.
# Keeps a single unusual filing from saturating the scale on its own.
INCIDENCE_FULL_SCALE = 1.0

# A genre needs this many documents before its own mean is trusted over its
# parent's, on the same principle as MIN_SECTOR_OBSERVATIONS.
MIN_DOCUMENTS_FOR_GENRE = 4

# Deliberately lighter than NORM_PRIOR_WEIGHT, which is 8.
#
# That constant was tuned for shrinking a *direction mean* toward zero, where the
# prior is weak and heavy shrinkage is the right caution. A per-document *count*
# is a different quantity: it is far more stable within a genre and wildly
# different between genres, so shrinking an annual report's expected eleven risk
# factors toward a corpus mean that includes earnings calls carrying one does not
# express caution, it imports the wrong genre's answer. At weight 8 a perfectly
# ordinary annual report scored as half a standard deviation negative.
#
# MIN_DOCUMENTS_FOR_GENRE already refuses a genre with too little data, so the
# prior here only has to smooth, not to guard.
INCIDENCE_PRIOR_WEIGHT = 2.0


@dataclass(frozen=True)
class Incidence:
    """How many directional findings a document of this genre normally carries."""

    positive: float
    negative: float
    documents: int

    @property
    def total(self) -> float:
        return self.positive + self.negative


@dataclass
class IncidenceNorms:
    """Expected directional finding counts per document, by genre and sector."""

    by_genre: dict[str, Incidence] = field(default_factory=dict)
    by_sector: dict[tuple[str, str], Incidence] = field(default_factory=dict)
    sector_of: dict[str, str] = field(default_factory=dict)
    root: Incidence = Incidence(0.0, 0.0, 0)

    def expected_for(self, genre: str, sector: Optional[str] = None) -> Incidence:
        """The most specific measured expectation for a document of this genre."""
        if sector:
            found = self.by_sector.get((sector, genre))
            if found is not None and found.documents >= MIN_DOCUMENTS_FOR_GENRE:
                return found
        found = self.by_genre.get(genre)
        if found is not None and found.documents >= MIN_DOCUMENTS_FOR_GENRE:
            return found
        return self.root


def _document_counts(signals: Iterable) -> dict[str, dict]:
    """Per document: its genre, its company, and its directional finding counts."""
    docs: dict[str, dict] = {}
    for signal in signals:
        key = document_key(signal)
        entry = docs.setdefault(key, {
            "genre": genre_of(signal),
            "company": str(getattr(signal, "company_id", "") or ""),
            "positive": 0, "negative": 0,
        })
        sign = sign_of(signal)
        if sign is None or sign == 0.0:
            continue
        entry["positive" if sign > 0 else "negative"] += 1
    return docs


def measure_incidence(
    signals: Iterable, sectors: Optional[dict] = None
) -> IncidenceNorms:
    """Measure how many directional findings a document normally carries.

    Pure, like `measure_norms`, and shrunk the same way: a sector's own mean is
    used where it rests on enough documents and inherits the genre's otherwise.
    """
    sectors = {str(k): v for k, v in (sectors or {}).items() if v}
    docs = _document_counts(signals)

    per_genre: dict[str, list[tuple[int, int]]] = defaultdict(list)
    per_sector: dict[tuple[str, str], list[tuple[int, int]]] = defaultdict(list)
    for entry in docs.values():
        pair = (entry["positive"], entry["negative"])
        per_genre[entry["genre"]].append(pair)
        sector = sectors.get(entry["company"])
        if sector:
            per_sector[(sector, entry["genre"])].append(pair)

    norms = IncidenceNorms(sector_of=dict(sectors))
    all_pairs = [pair for pairs in per_genre.values() for pair in pairs]
    if all_pairs:
        norms.root = Incidence(
            positive=sum(p for p, _ in all_pairs) / len(all_pairs),
            negative=sum(n for _, n in all_pairs) / len(all_pairs),
            documents=len(all_pairs),
        )

    for genre, pairs in per_genre.items():
        norms.by_genre[genre] = Incidence(
            positive=shrunk_mean([p for p, _ in pairs], norms.root.positive,
                                 INCIDENCE_PRIOR_WEIGHT),
            negative=shrunk_mean([n for _, n in pairs], norms.root.negative,
                                 INCIDENCE_PRIOR_WEIGHT),
            documents=len(pairs),
        )

    for (sector, genre), pairs in per_sector.items():
        parent = norms.by_genre.get(genre) or norms.root
        norms.by_sector[(sector, genre)] = Incidence(
            positive=shrunk_mean([p for p, _ in pairs], parent.positive,
                                 INCIDENCE_PRIOR_WEIGHT),
            negative=shrunk_mean([n for _, n in pairs], parent.negative,
                                 INCIDENCE_PRIOR_WEIGHT),
            documents=len(pairs),
        )
    return norms


def incidence_excess(
    signals: list, norms: IncidenceNorms, *, sector: Optional[str] = None
) -> tuple[Optional[float], dict]:
    """How unusual this company's volume of directional disclosure is.

    Returns a value in [-1, 1] and the workings behind it. Negative means the
    company disclosed more concern, or less reassurance, than documents of its
    kind normally carry. None means no document had a measurable genre.

    Aggregated per document and then averaged, so a company that filed three
    times is not counted as one long filing, and a single unusual document
    cannot carry a verdict on its own.
    """
    docs = _document_counts(signals)
    if not docs:
        return None, {"documents": 0}

    per_document = []
    workings = []
    for key, entry in docs.items():
        expected = norms.expected_for(entry["genre"], sector)
        if expected.documents < MIN_DOCUMENTS_FOR_GENRE:
            continue
        # A document that produced no directional finding at all is excluded,
        # and this guard is load-bearing rather than tidy.
        #
        # Without it, absence reads as good news: a filing carrying six quotes
        # and no risk factors was scored against a genre expecting five risk
        # factors and came out strongly positive. That is only a real signal if
        # the risk diff actually ran, and for a company with one stored filing it
        # cannot — there is no prior to compare against, so NEW_RISK_FACTOR is
        # unavailable by construction. Thirty-six of the forty-six companies Loom
        # has read are in exactly that position, so this would have manufactured
        # a positive verdict for most of the corpus out of missing data.
        #
        # Loom cannot distinguish "this filing disclosed little" from "this
        # filing was never compared", so it declines to read either as evidence.
        if entry["positive"] == 0 and entry["negative"] == 0:
            continue
        surprise_negative = entry["negative"] - expected.negative
        surprise_positive = entry["positive"] - expected.positive
        scale = max(expected.total, INCIDENCE_FULL_SCALE)
        value = max(-1.0, min(1.0, (surprise_positive - surprise_negative) / scale))
        per_document.append(value)
        workings.append({
            "document": key, "genre": entry["genre"],
            "observed_positive": entry["positive"],
            "observed_negative": entry["negative"],
            "expected_positive": round(expected.positive, 3),
            "expected_negative": round(expected.negative, 3),
            "residual": round(value, 4),
        })

    if not per_document:
        return None, {"documents": len(docs), "measurable": 0}

    mean = sum(per_document) / len(per_document)
    return mean, {
        "documents": len(docs),
        "measurable": len(per_document),
        "per_document": workings,
        "mean_residual": round(mean, 4),
    }
