"""How unusual a paragraph's language is against every filing Loom holds.

This is the independent baseline. `engine/disclosure.py` measures what documents
of a genre normally *say* from findings the model extracted, which means the
expectation and the extractor share a bias and agree about it by construction.
This module measures the same idea from raw filing text, so nothing in it passes
through a model and its answer can disagree with the extractor's.

**It is a feature and never a filter, and that distinction is the whole design.**

The obvious use would be to drop paragraphs that look like boilerplate. That
would be wrong, and the failure mode is specific: a risk that is boilerplate
across an industry can be the decisive fact for one filer the year it first
appears. "Our largest customer may not renew" is in hundreds of 10-Ks and is the
story of exactly one of them.

So nothing here discards anything. `diffing.py` already truncates its candidate
list at MAX_PARAGRAPHS_TO_ASSESS, ordered by how unlike the prior filing each
paragraph is — which means a decisive paragraph could already be lost by landing
just past the cap under a crude ordering. What this module does is improve *that*
ordering. It removes no paragraph that survived before, and it makes the
existing truncation better informed, so it reduces the risk of losing something
decisive rather than adding to it.

And where the two signals disagree, novelty to this company wins. A paragraph
written entirely in the industry's vocabulary that has never appeared in this
filer's own prior filing still ranks above a paragraph full of rare words that
the company has been carrying for years. See `diffing.rank_score`.

**Why terms rather than shingles or embeddings.** Filers reword boilerplate every
year, so exact-duplicate detection under-counts it. Embeddings would handle that
but need torch on a machine with 8GB of RAM and 2.4 of it already inside Docker.
Terms survive rewording, cost nothing, and the arithmetic is auditable: a reader
can be shown which words made a paragraph unusual.
"""

from __future__ import annotations

import logging
import math
import re
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.company import Company
from app.models.corpus_term import CorpusTerm
from app.models.document import RawDocument

logger = logging.getLogger(__name__)

# Sections worth indexing: the ones the comparison actually reads. Indexing whole
# filings would give slightly better frequency estimates and cost several times
# the work for terms no comparison will ever score.
INDEXED_SECTIONS = ("1A", "2")

# A term shorter than this carries no signal, and one longer is almost always a
# concatenation artefact from stripped markup.
MIN_TERM_LENGTH = 4
MAX_TERM_LENGTH = 32

# Terms appearing in only one company are kept out of the table: there are
# hundreds of thousands of them, they are mostly proper nouns and typography, and
# their *absence* from the table is the signal. A term the table does not know is
# treated as maximally rare, which is the correct reading and costs no storage.
MIN_COMPANIES_TO_STORE = 2

# How many of a paragraph's rarest terms decide its score.
#
# The mean over every term would be wrong. A paragraph is specific because of a
# few distinctive words — a jurisdiction, a counterparty, a named programme — set
# in ordinary prose, and averaging over the ordinary prose washes exactly those
# out. Taking the rarest few asks "does this paragraph contain anything unusual",
# which is the question.
RAREST_TERMS_CONSIDERED = 5

# How long a loaded vocabulary is reused. Longer than the norms cache because
# document frequency over a thousand companies barely moves when one filing
# arrives, whereas a company's own findings change its norms immediately.
TTL_SECONDS = 1800

_WORD = re.compile(r"[a-z][a-z'\-]+")

# Words that appear in essentially every filing and would otherwise dominate the
# term count without ever distinguishing anything. Deliberately short: the
# document-frequency weighting already demotes common words, and a long
# hand-written stoplist is an opinion about language that the corpus can measure
# for itself.
_STOPWORDS = frozenset({
    "that", "this", "with", "from", "have", "has", "been", "were", "was", "are",
    "its", "their", "which", "would", "could", "may", "might", "will", "shall",
    "such", "other", "these", "those", "than", "then", "there", "into", "upon",
    "any", "all", "our", "the", "and", "for", "not", "but", "his", "her",
})


def terms_of(text: str) -> set[str]:
    """The distinct content terms in a passage.

    A set rather than a count, because document frequency asks whether a company
    uses a term at all. Repetition inside one filing says something about style
    rather than about the term's commonness.
    """
    out: set[str] = set()
    for match in _WORD.finditer((text or "").lower()):
        word = match.group(0)
        if MIN_TERM_LENGTH <= len(word) <= MAX_TERM_LENGTH and word not in _STOPWORDS:
            out.add(word)
    return out


@dataclass
class Vocabulary:
    """Document frequency per term, and the corpus it was measured over.

    Holds no session, so scoring stays pure and testable without a database, on
    the same boundary `engine/disclosure.py` keeps.
    """

    document_frequency: dict[str, int] = field(default_factory=dict)
    companies: int = 0

    @property
    def measured(self) -> bool:
        """Whether there is enough corpus for a frequency to mean anything."""
        return self.companies >= 20 and bool(self.document_frequency)

    def term_rarity(self, term: str) -> float:
        """How unusual one term is, in [0, 1]. 1 means no other company uses it.

        Inverse document frequency, normalised by the corpus size so the scale
        does not shift as the corpus grows. A term absent from the table appeared
        in fewer than MIN_COMPANIES_TO_STORE companies, which is the rarest case
        there is, so absence scores 1 rather than being treated as unknown.
        """
        if not self.measured:
            return 0.0
        seen = self.document_frequency.get(term)
        if seen is None:
            return 1.0
        # log(N / df) / log(N): 1.0 for a term in one company, 0.0 for one in all.
        return max(0.0, min(1.0, math.log(self.companies / seen) / math.log(self.companies)))

    def rarity(self, paragraph: str) -> Optional[float]:
        """How unusual a paragraph's language is, in [0, 1], or None if unmeasurable.

        None rather than 0.0 when the corpus is too thin or the paragraph has no
        scorable terms: an unmeasured paragraph is not a boilerplate one, and a
        caller that wants to treat the two alike must do it knowingly.
        """
        if not self.measured:
            return None
        found = terms_of(paragraph)
        if not found:
            return None
        scores = sorted((self.term_rarity(t) for t in found), reverse=True)
        top = scores[:RAREST_TERMS_CONSIDERED]
        return sum(top) / len(top)

    def unusual_terms(self, paragraph: str, limit: int = 5) -> list[str]:
        """The terms that made a paragraph score as it did.

        Exists so the score can be shown with its reasons. A number a reader
        cannot interrogate is an assertion, and this one is cheap to justify.
        """
        if not self.measured:
            return []
        ranked = sorted(
            terms_of(paragraph), key=lambda t: self.term_rarity(t), reverse=True
        )
        return ranked[:limit]


# --------------------------------------------------------------- building


def _section_text(document, section_reader) -> str:
    """Every indexed section of one filing, concatenated."""
    from app.engine.sections import extract_section

    try:
        text = section_reader(document.blob_uri)
    except Exception:
        logger.debug("Corpus: could not read %s", document.blob_uri)
        return ""
    parts = []
    for section in INDEXED_SECTIONS:
        found = extract_section(text, section)
        if found:
            parts.append(found)
    return "\n".join(parts)


def build_vocabulary(
    db: Session, *, section_reader=None, company_limit: Optional[int] = None
) -> Vocabulary:
    """Measure document frequency across the stored corpus and persist it.

    Processed one company at a time rather than one filing at a time, for two
    reasons. Document frequency is counted over companies, so a company's terms
    have to be unioned before anything is incremented. And it bounds memory: only
    one company's term set is held at once, against a corpus that would otherwise
    need every term mapped to every company that used it.
    """
    if section_reader is None:
        from app.engine.pipeline import _document_text

        section_reader = _document_text

    rows = db.execute(
        select(RawDocument.company_id, RawDocument.blob_uri, RawDocument.doc_subtype)
        .where(RawDocument.doc_subtype.in_(("10-K", "10-Q")))
        .order_by(RawDocument.company_id)
    ).all()

    by_company: dict[object, list] = {}
    for company_id, blob_uri, doc_subtype in rows:
        by_company.setdefault(company_id, []).append(
            type("Doc", (), {"blob_uri": blob_uri, "doc_subtype": doc_subtype})()
        )

    company_ids = list(by_company)
    if company_limit:
        company_ids = company_ids[:company_limit]

    frequency: dict[str, int] = {}
    counted = 0
    for company_id in company_ids:
        seen: set[str] = set()
        for document in by_company[company_id]:
            text = _section_text(document, section_reader)
            if text:
                seen |= terms_of(text)
        if not seen:
            continue
        counted += 1
        for term in seen:
            frequency[term] = frequency.get(term, 0) + 1

    kept = {t: n for t, n in frequency.items() if n >= MIN_COMPANIES_TO_STORE}
    logger.info(
        "Corpus: %d companies indexed, %d distinct terms, %d stored (>=%d companies).",
        counted, len(frequency), len(kept), MIN_COMPANIES_TO_STORE,
    )

    # Replaced wholesale rather than merged. Document frequency is a property of
    # the whole corpus at one moment, and a half-updated table would mix two
    # corpus sizes into one ratio.
    db.query(CorpusTerm).delete(synchronize_session=False)
    db.bulk_save_objects(
        [CorpusTerm(term=t, companies=n) for t, n in kept.items()]
    )
    db.commit()

    return Vocabulary(document_frequency=kept, companies=counted)


# --------------------------------------------------------------- loading

_lock = threading.Lock()
_cached: Optional[tuple[datetime, Vocabulary]] = None


def load_vocabulary(db: Session, *, force: bool = False) -> Vocabulary:
    """The stored vocabulary, reused within its time-to-live.

    Never raises into a caller. Without a vocabulary the comparison falls back to
    the ordering it used before this module existed, which is the honest
    degradation: a worse ranking, not a wrong one.
    """
    global _cached
    now = datetime.now(timezone.utc)
    with _lock:
        if not force and _cached and now - _cached[0] < timedelta(seconds=TTL_SECONDS):
            return _cached[1]
    try:
        rows = db.execute(select(CorpusTerm.term, CorpusTerm.companies)).all()
        companies = db.execute(
            select(func.count(func.distinct(RawDocument.company_id)))
            .where(RawDocument.doc_subtype.in_(("10-K", "10-Q")))
        ).scalar() or 0
        vocabulary = Vocabulary(
            document_frequency={t: n for t, n in rows}, companies=int(companies)
        )
    except Exception:
        logger.exception("Corpus: could not load the vocabulary; ranking unweighted.")
        return Vocabulary()
    with _lock:
        _cached = (now, vocabulary)
    return vocabulary


def warm(db: Session) -> None:
    load_vocabulary(db, force=True)


__all__ = [
    "INDEXED_SECTIONS",
    "MIN_COMPANIES_TO_STORE",
    "RAREST_TERMS_CONSIDERED",
    "Vocabulary",
    "build_vocabulary",
    "load_vocabulary",
    "terms_of",
    "warm",
]
