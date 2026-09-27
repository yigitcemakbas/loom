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
    """+1, -1, 0, or None when the finding was never assessed.

    None rather than 0.0, for the reason the whole codebase keeps repeating:
    an unassessed finding is not a balanced one, and a caller that wants to
    treat it as neutral must do so knowingly.
    """
    return _SIGN.get(getattr(signal, "market_direction", None))


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
        weight = abs(excess)
        if parity is not None:
            weight *= parity.get(str(getattr(signal, "id", id(signal))), 1.0)
        if restated_ids and str(getattr(signal, "id", id(signal))) in restated_ids:
            weight *= RECURRENCE_DISCOUNT
        total += weight
    return total


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
    residuals = [
        abs(excess)
        for signal in signals
        if sign_of(signal) not in (None, 0.0)
        and (excess := norms.excess_for(signal)) is not None
    ]
    if not residuals:
        return None
    return max(0.0, 1.0 - sum(residuals) / len(residuals))


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
    "genre_of",
    "measure_norms",
    "parity_weights",
    "routine_share",
    "restated",
    "sign_of",
    "type_of",
]
