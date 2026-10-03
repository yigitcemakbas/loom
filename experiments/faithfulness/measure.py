"""Is Loom telling the truth about the documents it read?

Every measurement Loom makes of itself so far scores its findings against
forward returns. That is a claim the product explicitly disclaims making, and
at this sample size it has almost no statistical power: `engine/reliability.py`
reports zero of seven finding types clearing its sample floor, and the reader
benchmark's evidence-versus-control comparison came back a null.

This harness measures the claim Loom does make. A finding asserts something
checkable about a document: that this passage is in that filing, verbatim; that
this risk factor is new relative to the prior year; that this paragraph was
withdrawn. Each of those is verifiable today, deterministically, against text
already on disk. No model call, no quota, no waiting for returns to resolve.

Five checks, run over a sample of stored findings:

  quote_verbatim        the evidence quote occurs in the source text
  quote_placement       a new risk is in the current filing and absent from the
                        prior one; a withdrawn risk is the reverse
  provenance_resolvable the cited document exists and its text is readable
  not_future_dated      the document was published no later than the finding
  direction_consistent  the documentary direction matches what the kind means

A failure here is a defect a reader could have caught, which is the point: it
is the class of error that destroys trust in a research tool, and the only class
Loom can currently measure honestly.

Run:  python -m experiments.faithfulness.measure [--sample 400] [--kind new_risk_factor]
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import timezone
from pathlib import Path
from typing import Optional

# The bench scripts rely on being run with backend/ already importable. Doing
# it here instead means this runs the same from the repo root, from backend/, or
# through -m, which matters for something meant to be run routinely.
# Find whatever directory holds the `app` package and put it on the path.
#
# Two layouts have to work: the repo, where it is backend/app, and the running
# container, where it is /app/app. Running a script by path puts the script's
# own directory on sys.path rather than the working directory, so neither
# resolves on its own and the search is not redundant.
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
from app.engine.diffing import (  # noqa: E402
    SIMILARITY_THRESHOLD,
    best_similarity,
)
from app.engine.direction import documentary_sign  # noqa: E402
from app.engine.sections import extract_section, split_paragraphs  # noqa: E402
from app.engine.pipeline import (  # noqa: E402
    COMPARISON_PLAN,
    _document_text,
)
from app.repositories.document_repository import DocumentRepository  # noqa: E402
from app.models.document import RawDocument  # noqa: E402
from app.models.signal import Signal  # noqa: E402

# A quote this short matches by accident. "Revenue increased" is in every filing
# ever written, so counting it as evidence of faithful extraction would inflate
# the pass rate with findings that assert nothing locatable.
MIN_MEANINGFUL_QUOTE = 40

# What each kind claims about where its quote lives. A new risk factor is a
# paragraph the current filing has and the prior one did not; a resolved one is
# the exact inverse, and its text therefore exists only in the earlier document.
# Getting this backwards is why the check is a table rather than an assumption.
PLACEMENT = {
    "new_risk_factor": ("current", "absent_from_prior"),
    "resolved_risk_factor": ("prior", "absent_from_current"),
}

# The kinds whose direction is definitional rather than judged. A new risk that
# scores positive is not a close call, it is a bug.
EXPECTED_SIGN = {
    "new_risk_factor": -1.0,
    "resolved_risk_factor": 1.0,
}

_QUOTE_CHARS = str.maketrans({
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"', "‟": '"',
    "–": "-", "—": "-", "−": "-", " ": " ",
})


def normalise(text: str) -> str:
    """Collapse the differences that are not the model's fault.

    Filing text arrives with hard-wrapped lines, non-breaking spaces and curly
    punctuation that extraction routinely straightens. Treating those as
    infidelity would fail honest quotes and bury the real fabrications, so they
    are normalised away and nothing else is: wording, order and content are
    compared exactly.
    """
    text = unicodedata.normalize("NFKC", text).translate(_QUOTE_CHARS)
    return re.sub(r"\s+", " ", text).strip()


def is_tabular(quote: str) -> bool:
    """Whether a quote is table content rather than prose.

    Reported separately because the two fail for different reasons. A financial
    table survives text extraction as a run of numbers whose column structure is
    gone, and whatever whitespace the extractor emitted is not necessarily what
    the model was shown, so a mismatch there says little about whether the model
    invented anything. A prose mismatch is a much stronger signal, and averaging
    the two produced a single rate that overstated the prose problem and
    understated the table one.
    """
    if "following table" in quote.lower():
        return True
    stripped = re.sub(r"\s", "", quote)
    if not stripped:
        return False
    digits = sum(c.isdigit() for c in stripped)
    return digits / len(stripped) > 0.15


def containing_paragraph(quote: str, paragraphs: list[str]) -> Optional[str]:
    """The paragraph a quote was taken from, by normalised containment."""
    for para in paragraphs:
        if quote in normalise(para):
            return para
    return None


def _section_paragraphs(cache: dict, document, section: str) -> list[str]:
    """The named section of a filing, split the way the engine splits it.

    Deliberately reuses `extract_section` and `split_paragraphs` rather than
    re-splitting: the question this harness asks is whether the engine's own
    decision was right, and answering it against different paragraph boundaries
    would measure something else.
    """
    if document is None:
        return []
    key = f"paras:{document.id}:{section}"
    if key not in cache:
        try:
            raw = cache.get(f"raw:{document.id}")
            body = extract_section(raw, section) if raw else None
            cache[key] = split_paragraphs(body) if body else []
        except Exception:
            cache[key] = []
    return cache[key]


@dataclass
class Result:
    checks: Counter = field(default_factory=Counter)
    failures: list[dict] = field(default_factory=list)
    by_kind: dict = field(default_factory=lambda: defaultdict(Counter))

    def record(self, name: str, passed: Optional[bool], kind: str, detail: Optional[dict] = None):
        """None means the check did not apply, which is not a pass.

        Kept distinct because a harness that scores inapplicable checks as
        passing reports a number that rises as coverage falls.
        """
        if passed is None:
            self.checks[f"{name}:n/a"] += 1
            self.by_kind[kind][f"{name}:n/a"] += 1
            return
        key = f"{name}:{'pass' if passed else 'fail'}"
        self.checks[key] += 1
        self.by_kind[kind][key] += 1
        if not passed and detail:
            self.failures.append({"check": name, "kind": kind, **detail})

    def rate(self, name: str) -> Optional[float]:
        ok, bad = self.checks[f"{name}:pass"], self.checks[f"{name}:fail"]
        return None if ok + bad == 0 else ok / (ok + bad)


def _prior_filing(db, document: RawDocument) -> Optional[RawDocument]:
    """The filing the engine compared against, via the engine's own lookup.

    Calls `DocumentRepository.find_prior_filing` rather than reimplementing it.
    An earlier version of this harness wrote its own query and silently answered
    a different question than the one the pipeline asks, which is the failure
    mode a self-measurement harness has to avoid above all others: it reports
    confident numbers about a decision the product never made.
    """
    occurred_at = document.published_at or document.fetched_at
    return DocumentRepository(db).find_prior_filing(
        document.company_id, document.doc_subtype, occurred_at
    )


def _compared_section(document: RawDocument) -> Optional[str]:
    """Which section the engine diffed for this filing's form.

    Read from COMPARISON_PLAN, because it differs by cadence and getting it
    wrong invalidates the check: a 10-K is compared on Item 1A risk factors, a
    10-Q on Item 2 management's discussion, since a 10-Q's own risk section is
    normally a cross reference back to the annual report. This harness hardcoded
    1A at first and so reconstructed the wrong text for every finding sourced
    from a quarterly report.
    """
    plan = COMPARISON_PLAN.get(document.doc_subtype or "")
    return plan["section"] if plan else None


def _text(cache: dict, document: Optional[RawDocument]) -> Optional[str]:
    """Normalised full text, with the raw form kept alongside it.

    Both are needed and they are not interchangeable: quote matching wants
    whitespace collapsed, while `extract_section` locates item headings by line
    structure and finds nothing once that is flattened.
    """
    if document is None or not document.blob_uri:
        return None
    key = str(document.id)
    if key not in cache:
        try:
            raw = _document_text(document.blob_uri)
            cache[f"raw:{key}"] = raw
            cache[key] = normalise(raw)
        except Exception:
            cache[f"raw:{key}"] = None
            cache[key] = None
    return cache[key]


def measure(db, sample: int, kind_filter: Optional[str]) -> Result:
    query = (
        select(Signal, RawDocument)
        .join(RawDocument, RawDocument.id == Signal.source_document_id)
        .where(Signal.dismissed_at.is_(None))
        .where(Signal.evidence_quote.is_not(None))
    )
    rows = db.execute(query).all()
    if kind_filter:
        rows = [r for r in rows if str(getattr(r[0].signal_type, "value", r[0].signal_type)) == kind_filter]

    random.seed(20261002)
    if len(rows) > sample:
        rows = random.sample(rows, sample)

    result = Result()
    cache: dict = {}

    for signal, document in rows:
        kind = str(getattr(signal.signal_type, "value", signal.signal_type))
        quote = normalise(signal.evidence_quote or "")
        current = _text(cache, document)

        # --- provenance -----------------------------------------------------
        result.record("provenance_resolvable", current is not None, kind,
                      {"signal": str(signal.id), "why": "source document text unreadable"})
        if current is None:
            continue

        # --- future dating --------------------------------------------------
        published, occurred = document.published_at, signal.occurred_at
        if published is None:
            result.record("not_future_dated", None, kind)
        else:
            if published.tzinfo is None:
                published = published.replace(tzinfo=timezone.utc)
            occ = occurred.replace(tzinfo=timezone.utc) if occurred.tzinfo is None else occurred
            result.record("not_future_dated", published <= occ, kind, {
                "signal": str(signal.id),
                "why": f"document published {published.date()} after finding dated {occ.date()}",
            })

        # --- the quote ------------------------------------------------------
        if len(quote) < MIN_MEANINGFUL_QUOTE:
            result.record("quote_verbatim", None, kind)
            result.record("quote_placement", None, kind)
            continue

        prior = _prior_filing(db, document)
        prior_text = _text(cache, prior)
        in_current = quote in current
        in_prior = bool(prior_text and quote in prior_text)

        # A withdrawn paragraph is legitimately absent from the filing that
        # reported it, so "found in either document" is the honest test of
        # whether the text was fabricated.
        found = in_current or in_prior
        if not found:
            loose = quote.casefold() in current.casefold() or bool(
                prior_text and quote.casefold() in prior_text.casefold()
            )
            found = loose
        check = "quote_verbatim_tabular" if is_tabular(quote) else "quote_verbatim_prose"
        result.record(check, found, kind, {
            "signal": str(signal.id),
            "ticker_company": str(signal.company_id),
            "why": "quote appears in neither the cited filing nor its predecessor",
            "quote": (signal.evidence_quote or "")[:220],
        })

        # --- placement ------------------------------------------------------
        #
        # Asks the engine's own question rather than a proxy for it: locate the
        # paragraph the quote came from, then score it against the prior
        # filing's paragraphs exactly as `_unmatched` does. A finding is wrong
        # when that score clears SIMILARITY_THRESHOLD, because the engine should
        # then have excluded the paragraph as unchanged.
        #
        # An earlier version tested whether the quote appeared in both filings.
        # That over-reported: a genuinely new paragraph can reuse a sentence
        # from elsewhere in last year's filing, and the quote is a span the
        # model chose rather than the unit the comparison ran on.
        expected = PLACEMENT.get(kind)
        if expected is None:
            result.record("quote_placement", None, kind)
        else:
            where, _ = expected
            section = _compared_section(document)
            holder = document if where == "current" else prior
            others = prior if where == "current" else document
            paragraphs = _section_paragraphs(cache, holder, section) if section else []
            against = _section_paragraphs(cache, others, section) if section else []
            para = containing_paragraph(quote, paragraphs) if paragraphs else None

            if para is None or not against:
                # The section could not be located in one of the two filings, so
                # the engine's decision is not reconstructible. Not a pass.
                result.record("quote_placement", None, kind)
            else:
                score = best_similarity(para, against)
                result.record("quote_placement", score < SIMILARITY_THRESHOLD, kind, {
                    "signal": str(signal.id),
                    "why": (
                        f"{kind}: containing paragraph scores {score:.2f} against the "
                        f"{'prior' if where == 'current' else 'current'} filing, at or above "
                        f"the {SIMILARITY_THRESHOLD} threshold, so it should not have been "
                        f"reported as a change"
                    ),
                    "quote": (signal.evidence_quote or "")[:220],
                })

        # --- direction ------------------------------------------------------
        want = EXPECTED_SIGN.get(kind)
        if want is None:
            result.record("direction_consistent", None, kind)
        else:
            got = documentary_sign(signal)
            result.record("direction_consistent", got == want, kind, {
                "signal": str(signal.id),
                "why": f"{kind} should score {want}, scored {got}",
            })

    return result


def report(result: Result) -> str:
    names = ["quote_verbatim_prose", "quote_verbatim_tabular", "quote_placement",
             "provenance_resolvable", "not_future_dated", "direction_consistent"]
    lines = ["", "FAITHFULNESS", "=" * 72, ""]
    lines.append(f"{'check':<24}{'pass':>7}{'fail':>7}{'n/a':>7}{'rate':>9}")
    lines.append("-" * 72)
    for name in names:
        ok = result.checks[f"{name}:pass"]
        bad = result.checks[f"{name}:fail"]
        na = result.checks[f"{name}:n/a"]
        rate = result.rate(name)
        shown = "n/a" if rate is None else f"{rate:6.1%}"
        lines.append(f"{name:<24}{ok:>7}{bad:>7}{na:>7}{shown:>9}")

    if result.failures:
        lines += ["", f"FAILURES ({len(result.failures)}), first 12", "-" * 72]
        for f in result.failures[:12]:
            lines.append(f"  [{f['check']}] {f['kind']}")
            lines.append(f"    {f['why']}")
            if f.get("quote"):
                lines.append(f"    quote: {f['quote'][:160]}")
    else:
        lines += ["", "No failures in the sample."]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", type=int, default=400)
    parser.add_argument("--kind", default=None)
    parser.add_argument("--out", default="experiments/faithfulness/results.json")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        result = measure(db, args.sample, args.kind)
    finally:
        db.close()

    print(report(result))
    payload = {
        "checks": dict(result.checks),
        "rates": {n: result.rate(n) for n in
                  ("quote_verbatim_prose", "quote_verbatim_tabular", "quote_placement",
                   "provenance_resolvable", "not_future_dated", "direction_consistent")},
        "by_kind": {k: dict(v) for k, v in result.by_kind.items()},
        "failures": result.failures,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2))
    print(f"Written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
