"""Year-over-year risk factor comparison.

Two stages, deliberately in this order:

1. Deterministic. Extract Item 1A from both filings, split into comparable
   paragraphs, and match them by text similarity. Anything that matches is
   unchanged and is discarded here, no LLM ever sees it.
2. Language judgement. Only the unmatched paragraphs go to the model, which
   decides whether each is a substantive new risk or just a rewrite.

The ordering is what makes the feature affordable. Two 10-Ks are ~200k tokens
together; the paragraphs that actually differ are usually a few thousand. It
also makes the result auditable: every finding points at a specific paragraph
that provably has no close match in the prior filing.
"""

import logging
from dataclasses import dataclass, field
from difflib import SequenceMatcher

from app.engine.sections import extract_section, split_paragraphs

logger = logging.getLogger(__name__)

# Above this similarity, two paragraphs are treated as the same risk restated.
# Chosen to tolerate the light annual rewording filers apply to boilerplate
# while still catching genuinely new text.
SIMILARITY_THRESHOLD = 0.75

# Guard against a pathological diff (e.g. a filer restructuring the whole
# section) turning into one enormous prompt. Measured against real filings the
# genuine count runs 30-41 paragraphs, so this sits just above the observed
# maximum: high enough to be lossless in practice, and ~7k tokens even when
# fully used, which is a few cents. When it does bite, the least-similar
# paragraphs are kept, since those are the likeliest to be genuinely new.
MAX_PARAGRAPHS_TO_ASSESS = 45


def _normalize(text: str) -> str:
    """Compare on lowercase alphanumerics so punctuation and spacing churn
    doesn't read as a real change."""
    return " ".join("".join(c if c.isalnum() else " " for c in text.lower()).split())


def best_similarity(paragraph: str, candidates: list[str]) -> float:
    """Highest similarity between `paragraph` and any candidate."""
    target = _normalize(paragraph)
    if not target:
        return 1.0  # empty content is never "new"
    best = 0.0
    matcher = SequenceMatcher()
    matcher.set_seq2(target)
    for candidate in candidates:
        normalized = _normalize(candidate)
        # Cheap length gate first: SequenceMatcher is the expensive part, and
        # paragraphs of very different length cannot clear the threshold.
        shorter, longer = sorted((len(normalized), len(target)))
        if longer == 0 or shorter / longer < SIMILARITY_THRESHOLD:
            continue
        matcher.set_seq1(normalized)
        best = max(best, matcher.ratio())
        if best >= 0.99:
            break
    return best


@dataclass
class SectionDiff:
    """What changed in a section, in both directions.

    `removed` exists because for most of this engine's life it did not, and the
    omission was structural rather than cosmetic. The comparison only ever
    iterated the current filing looking for paragraphs with no match in the
    prior one, so Loom could see a risk appear and never see one resolve. Every
    finding the risk diff could produce was therefore negative, which made the
    verdict negative by construction: the stance had no way to improve except by
    the company saying something reassuring elsewhere.

    A paragraph present last year and absent now is the company withdrawing a
    disclosure it previously felt obliged to make. That is not automatically good
    news — it can be a consolidation or a rewrite — which is exactly why it goes
    to the same language judgement the additions already go through, rather than
    being counted as a positive on the strength of the deterministic step alone.
    """

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    current_total: int = 0
    prior_total: int = 0


def _unmatched(source: list[str], against: list[str]) -> list[str]:
    """Paragraphs in `source` with no close match in `against`, least similar
    first, capped so a restructured section cannot become one enormous prompt."""
    scored = [(best_similarity(para, against), para) for para in source]
    changed = sorted(
        ((sim, para) for sim, para in scored if sim < SIMILARITY_THRESHOLD),
        key=lambda pair: pair[0],
    )
    return [para for _, para in changed[:MAX_PARAGRAPHS_TO_ASSESS]]


def diff_section(
    current_text: str, prior_text: str, section: str = "1A"
) -> SectionDiff:
    """Compare a section in both directions in one pass.

    One pass because the expensive parts — extracting the section and splitting it
    into comparable paragraphs — are shared, and doing them twice to get the
    symmetric answer would double the cost of the cheap half of the feature.
    """
    current_section = extract_section(current_text, section)
    prior_section = extract_section(prior_text, section)
    if current_section is None or prior_section is None:
        logger.info("Item %s missing from one of the filings; skipping diff.", section)
        return SectionDiff()

    current = split_paragraphs(current_section)
    prior = split_paragraphs(prior_section)
    if not current or not prior:
        return SectionDiff(current_total=len(current), prior_total=len(prior))

    return SectionDiff(
        added=_unmatched(current, prior),
        removed=_unmatched(prior, current),
        current_total=len(current),
        prior_total=len(prior),
    )


def find_changed_paragraphs(
    current_text: str, prior_text: str, section: str = "1A"
) -> tuple[list[str], int, int]:
    """Return (unmatched_current_paragraphs, current_total, prior_total).

    Kept as the additions-only view over `diff_section`, because several callers
    and tests want exactly that and the symmetric result would change their
    meaning. New code should prefer `diff_section`.

    Unmatched paragraphs are candidates for a real change, not conclusions.
    Deciding whether a candidate is substantive is the model's job.

    `section` is parameterised because the interesting comparison differs by
    filing. In an annual report it is Item 1A, the risk factors. In a quarterly
    report the risk factors are usually a one-line "no material changes" cross
    reference, and the section that actually moves is Item 2, management's own
    discussion of the quarter's results.
    """
    diff = diff_section(current_text, prior_text, section)
    return diff.added, diff.current_total, diff.prior_total