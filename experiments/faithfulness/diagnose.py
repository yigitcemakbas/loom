"""Why can't these quotes be found in the filings they cite?

The faithfulness harness puts 5.5% of prose evidence quotes in neither the cited
filing nor its predecessor. That is the most serious defect class Loom can have:
the product's headline claim is that every conclusion resolves to a source
document and a verbatim passage, and an unlocatable quote is not a wrong answer
but an uncheckable one.

"Not found" has several possible causes with completely different fixes, so this
separates them instead of reporting a rate:

  wrong_document     the quote is verbatim in another document of the same
                     company, so the text is real and source_document_id is
                     pointing at the wrong filing
  truncated_tail     a long prefix matches but the ending does not, which is a
                     quote the model continued past where the filing stopped
  stitched           both halves appear in the document but not adjacently: two
                     passages joined into one apparent sentence
  paraphrased        high word overlap, no contiguous match of length: the model
                     rewrote rather than quoted
  absent             little or no overlap, nothing resembling it in the filing

Deterministic throughout. No model calls.

Run:  python experiments/faithfulness/diagnose.py [--sample 1200]
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import Counter
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

for _root in (*Path(__file__).resolve().parents, Path.cwd()):
    for _candidate in (_root / "backend", _root):
        if (_candidate / "app" / "__init__.py").is_file():
            sys.path.insert(0, str(_candidate))
            break
    else:
        continue
    break

from sqlalchemy import select  # noqa: E402

from app.db.session import SessionLocal  # noqa: E402
from app.engine.pipeline import _document_text  # noqa: E402
from app.models.document import RawDocument  # noqa: E402
from app.models.signal import Signal  # noqa: E402
from app.repositories.document_repository import DocumentRepository  # noqa: E402

MIN_MEANINGFUL_QUOTE = 40
PREFIX = 60

_QUOTE_CHARS = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
    "­": "", "…": "...",
})


def normalise(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).translate(_QUOTE_CHARS)
    return re.sub(r"\s+", " ", text).strip()


def is_tabular(quote: str) -> bool:
    if "following table" in quote.lower():
        return True
    stripped = re.sub(r"\s", "", quote)
    if not stripped:
        return False
    return sum(c.isdigit() for c in stripped) / len(stripped) > 0.15


def longest_match(quote: str, text: str) -> int:
    """Length of the longest passage of the quote that appears contiguously.

    The discriminator between a near-miss and an invention. A paraphrase shares
    vocabulary with the source but breaks into short runs; a genuine quote with a
    bad ending keeps one long run.
    """
    matcher = SequenceMatcher(None, quote, text, autojunk=False)
    return matcher.find_longest_match(0, len(quote), 0, len(text)).size


def word_overlap(quote: str, text: str) -> float:
    words = {w for w in re.findall(r"[a-z]{4,}", quote.lower())}
    if not words:
        return 0.0
    present = {w for w in words if w in text.lower()}
    return len(present) / len(words)


def classify(quote: str, cited: str, others: dict[str, str]) -> tuple[str, dict]:
    """Name the cause, preferring the explanations that are checkable."""
    # Real text in the wrong place is the first thing to rule out, because it is
    # a provenance bug rather than an extraction one and the fix is different.
    for doc_id, text in others.items():
        if quote in text:
            return "wrong_document", {"found_in": doc_id}

    prefix = quote[:PREFIX]
    longest = longest_match(quote, cited)
    overlap = word_overlap(quote, cited)
    facts = {
        "longest_contiguous": longest,
        "quote_length": len(quote),
        "match_fraction": round(longest / max(len(quote), 1), 3),
        "word_overlap": round(overlap, 3),
    }

    if prefix in cited:
        return "truncated_tail", facts

    # Two halves present separately but not together: a stitched quote.
    half = len(quote) // 2
    if quote[:half].strip() in cited and quote[half:].strip() in cited:
        return "stitched", facts

    if overlap >= 0.8 and longest < len(quote) * 0.5:
        return "paraphrased", facts
    if overlap >= 0.5:
        return "paraphrased", facts
    return "absent", facts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=1200)
    parser.add_argument("--show", type=int, default=8)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        rows = db.execute(
            select(Signal, RawDocument)
            .join(RawDocument, RawDocument.id == Signal.source_document_id)
            .where(Signal.dismissed_at.is_(None))
            .where(Signal.evidence_quote.is_not(None))
            .limit(args.sample)
        ).all()

        repo = DocumentRepository(db)
        text_cache: dict[str, Optional[str]] = {}

        def text_of(document) -> Optional[str]:
            key = str(document.id)
            if key not in text_cache:
                try:
                    text_cache[key] = normalise(_document_text(document.blob_uri))
                except Exception:
                    text_cache[key] = None
            return text_cache[key]

        causes = Counter()
        examples: dict[str, list] = {}
        checked = 0

        for signal, document in rows:
            quote = normalise(signal.evidence_quote or "")
            if len(quote) < MIN_MEANINGFUL_QUOTE or is_tabular(quote):
                continue
            cited = text_of(document)
            if cited is None:
                continue
            checked += 1
            if quote in cited:
                continue

            prior = repo.find_prior_filing(
                document.company_id, document.doc_subtype,
                document.published_at or document.fetched_at,
            )
            others = {}
            if prior is not None:
                prior_text = text_of(prior)
                if prior_text:
                    others[f"prior {prior.doc_subtype} {prior.published_at.date()}"] = prior_text
            if quote in "".join(others.values()):
                continue  # legitimately sourced from the compared filing

            # Any other filing this company has, to separate a provenance bug
            # from an extraction one.
            for other in db.execute(
                select(RawDocument)
                .where(RawDocument.company_id == document.company_id)
                .where(RawDocument.id != document.id)
                .limit(25)
            ).scalars().all():
                if prior is not None and other.id == prior.id:
                    continue
                body = text_of(other)
                if body:
                    others[f"{other.doc_subtype} {other.published_at.date() if other.published_at else '?'}"] = body

            cause, facts = classify(quote, cited, others)
            causes[cause] += 1
            examples.setdefault(cause, []).append({
                "signal": str(signal.id), "kind": str(getattr(signal.signal_type, "value", signal.signal_type)),
                "form": document.doc_subtype, "quote": signal.evidence_quote or "", **facts,
            })

        total_bad = sum(causes.values())
        print(f"\nPROSE QUOTE DIAGNOSIS   ({checked} prose quotes checked, "
              f"{total_bad} unlocatable, {total_bad / max(checked,1):.1%})")
        print("=" * 72)
        for cause, n in causes.most_common():
            print(f"  {cause:<18}{n:>4}   {n / max(total_bad,1):>6.1%}")

        for cause, items in examples.items():
            print(f"\n--- {cause} " + "-" * (66 - len(cause)))
            for item in items[:args.show]:
                print(f"  {item['signal'][:8]}  {item['kind']}  {item['form']}")
                if "found_in" in item:
                    print(f"    verbatim in: {item['found_in']}")
                else:
                    print(f"    longest contiguous match {item['longest_contiguous']}/"
                          f"{item['quote_length']} chars ({item['match_fraction']:.0%}), "
                          f"word overlap {item['word_overlap']:.0%}")
                print(f"    {item['quote'][:180]}")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
