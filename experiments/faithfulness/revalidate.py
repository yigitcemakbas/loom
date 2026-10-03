"""Retire stored risk-diff findings that the current engine would not produce.

The faithfulness harness found that 12 of 30 checkable risk-diff findings assert
a paragraph is new when an identical or near-identical one sits in the filing it
was compared against. Re-running the comparison with today's code emits none of
them: they are artifacts of engine versions since replaced, concentrated in
findings written in August. September's fail at roughly a tenth the rate.

They are still in the database, and the evidence API is still serving them to
agents as `new_risk_factor` with `how_established` reading "deterministic
comparison of two filings; checkable against the source text". That sentence is
the problem. The claim invites verification, and a reader who verifies finds the
paragraph in last year's filing.

This re-runs `diffing.diff_section` for each stored risk-diff finding, exactly as
the pipeline does, and dismisses the ones whose evidence no longer appears in the
added set. Deterministic throughout: no model call, no quota, and nothing
rewritten. `dismissed_at` is the column the schema already reserves for hiding
noise, so a retired finding stays auditable instead of vanishing.

Findings whose comparison cannot be reconstructed are left alone. A missing
section or an absent predecessor means this cannot tell a stale finding from a
sound one, and deleting on an inability to check would be worse than the problem.

Run:  python experiments/faithfulness/revalidate.py            # dry run
      python experiments/faithfulness/revalidate.py --apply
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

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
from app.engine import corpus, diffing  # noqa: E402
from app.engine.pipeline import COMPARISON_PLAN, _document_text  # noqa: E402
from app.engine.sections import extract_section, split_paragraphs  # noqa: E402
from app.models.document import RawDocument  # noqa: E402
from app.models.signal import Signal, SignalType  # noqa: E402
from app.repositories.document_repository import DocumentRepository  # noqa: E402

# Only the kinds whose entire claim is "this text is present in one filing and
# absent from the other". Everything else is a judgement this cannot adjudicate.
REVALIDATED = {SignalType.NEW_RISK_FACTOR}
_RESOLVED = getattr(SignalType, "RESOLVED_RISK_FACTOR", None)
if _RESOLVED is not None:
    REVALIDATED.add(_RESOLVED)


def normalise(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip()


def _paragraphs(cache: dict, document, section: str) -> list[str]:
    """A filing section split the way the engine splits it."""
    key = f"{document.id}:{section}"
    if key not in cache:
        try:
            body = extract_section(_document_text(document.blob_uri), section)
            cache[key] = split_paragraphs(body) if body else []
        except Exception:
            cache[key] = []
    return cache[key]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="Dismiss the stale findings. Without it, only reports.")
    parser.add_argument("--limit", type=int, default=2000)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        rows = db.execute(
            select(Signal, RawDocument)
            .join(RawDocument, RawDocument.id == Signal.source_document_id)
            .where(Signal.signal_type.in_(REVALIDATED))
            .where(Signal.dismissed_at.is_(None))
            .where(Signal.evidence_quote.is_not(None))
            .limit(args.limit)
        ).all()

        vocabulary = corpus.load_vocabulary(db)
        repo = DocumentRepository(db)
        cache: dict = {}

        stale, sound, unreconstructible = [], 0, 0

        for signal, document in rows:
            plan = COMPARISON_PLAN.get(document.doc_subtype or "")
            prior = repo.find_prior_filing(
                document.company_id, document.doc_subtype,
                document.published_at or document.fetched_at,
            )
            if plan is None or prior is None:
                unreconstructible += 1
                continue

            # Staleness has to be established positively, by finding the
            # paragraph's twin in the other filing. The tempting test — "is it
            # missing from diff.added?" — is wrong: `_unmatched` truncates its
            # result at MAX_PARAGRAPHS_TO_ASSESS, so a genuinely new paragraph
            # ranked below the cap is absent from `added` while being perfectly
            # sound. Dismissing on absence would have retired real findings.
            new_kind = signal.signal_type == SignalType.NEW_RISK_FACTOR
            holder, other = (document, prior) if new_kind else (prior, document)
            paragraphs = _paragraphs(cache, holder, plan["section"])
            against = _paragraphs(cache, other, plan["section"])
            if not paragraphs or not against:
                unreconstructible += 1
                continue

            quote = normalise(signal.evidence_quote or "")
            para = next((p for p in paragraphs if quote in normalise(p)), None)
            if para is None:
                # The quote is not in the section this finding claims to come
                # from, which is a different defect and not one to act on here.
                unreconstructible += 1
                continue

            if diffing.best_similarity(para, against) >= diffing.SIMILARITY_THRESHOLD:
                stale.append((signal, document))
            else:
                sound += 1

        print(f"\nREVALIDATION  ({len(rows)} risk-diff findings examined)")
        print("=" * 68)
        print(f"  still produced by current code : {sound}")
        print(f"  stale, no longer produced      : {len(stale)}")
        print(f"  not reconstructible, untouched : {unreconstructible}")
        checkable = sound + len(stale)
        if checkable:
            print(f"  stale share of checkable       : {len(stale) / checkable:.1%}")

        for signal, document in stale[:10]:
            print(f"\n  {str(signal.id)[:8]}  {document.doc_subtype}  "
                  f"created {signal.created_at.date()}")
            print(f"    {(signal.evidence_quote or '')[:140]}")

        if stale and args.apply:
            now = datetime.now(timezone.utc)
            for signal, _ in stale:
                signal.dismissed_at = now
            db.commit()
            print(f"\nDismissed {len(stale)} findings.")
        elif stale:
            print(f"\nDry run. Re-run with --apply to dismiss {len(stale)}.")
        else:
            print("\nNothing stale.")
    finally:
        db.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
