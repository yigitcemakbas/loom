"""Recording what the model decided about each paragraph, accepted or not.

The rejections are the point. Loom's risk comparison asks the model, per
paragraph, whether a change is substantive; only the substantive ones became
signals, and the rest were discarded by an early `continue`. The quota had
already been spent producing those judgements, so the corpus ended up with 262
positive examples, zero negatives, and no way to train a classifier on it.

Writes only. Nothing in the engine reads this, and that is deliberate: a label
store on the read path would make the verdict partly a function of its own
history, which is the kind of feedback loop that is invisible until it is
load-bearing.
"""

from __future__ import annotations

import hashlib
import logging
from typing import Iterable, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.assessment_label import AssessmentLabel

logger = logging.getLogger(__name__)

KIND_NEW_RISK = "new_risk"
KIND_RESOLVED_RISK = "resolved_risk"
KIND_QUARTER_CHANGE = "quarter_change"


def quote_hash(quote: str) -> str:
    """Stable identity for a paragraph, normalised the way the diff compares them.

    Normalised rather than hashed raw, so that a filer re-spacing a paragraph
    does not create a second label for the same text. Matches the intent of
    `diffing._normalize` without importing it, because this hash has to stay
    stable even if the comparison's tolerance is later retuned.
    """
    collapsed = " ".join(
        "".join(c if c.isalnum() else " " for c in (quote or "").lower()).split()
    )
    return hashlib.sha256(collapsed.encode("utf-8")).hexdigest()


def record_assessments(
    db: Session,
    *,
    company_id,
    document_id,
    compared_document_id,
    kind: str,
    section: Optional[str],
    assessments: Iterable,
    accepted_attr: str,
    model: Optional[str] = None,
    prompt_version: Optional[str] = None,
) -> int:
    """Store one row per assessed paragraph. Returns how many were new.

    `accepted_attr` is the field naming the model's decision, because the two
    prompts ask different questions: `is_substantive` for an added paragraph and
    `is_resolved` for a withdrawn one. Passing the attribute name keeps this
    function ignorant of both schemas rather than branching on them.

    Never raises into the caller, and never rolls back more than its own work.
    This is bookkeeping alongside analysis that has already succeeded and cost
    quota, so losing a filing's findings because the label write failed would be a
    bad trade — and a bare `db.rollback()` here would do exactly that, since this
    runs inside `analyze_document` before the signals are persisted. The savepoint
    is what keeps a unique-constraint collision from discarding the caller's
    transaction along with this one's.
    """
    rows = list(assessments or [])
    if not rows:
        return 0

    savepoint = db.begin_nested()
    try:
        existing = {
            h for (h,) in db.execute(
                select(AssessmentLabel.quote_hash)
                .where(AssessmentLabel.document_id == document_id)
                .where(AssessmentLabel.kind == kind)
            ).all()
        }

        written = 0
        for item in rows:
            quote = getattr(item, "quote", None)
            if not quote:
                continue
            digest = quote_hash(quote)
            if digest in existing:
                continue
            existing.add(digest)
            db.add(AssessmentLabel(
                company_id=company_id,
                document_id=document_id,
                compared_document_id=compared_document_id,
                kind=kind,
                section=section,
                quote=quote,
                quote_hash=digest,
                accepted=bool(getattr(item, accepted_attr, False)),
                confidence=getattr(item, "confidence", None),
                model=model,
                prompt_version=prompt_version,
            ))
            written += 1

        savepoint.commit()
        accepted = sum(1 for r in rows if bool(getattr(r, accepted_attr, False)))
        logger.info(
            "Labels: %s stored %d of %d (%d accepted, %d rejected).",
            kind, written, len(rows), accepted, len(rows) - accepted,
        )
        return written
    except Exception:
        logger.exception("Labels: failed to record %s assessments", kind)
        try:
            savepoint.rollback()
        except Exception:
            # The savepoint was already released, which means the failure was in
            # committing it. Nothing further to undo here, and raising now would
            # defeat the purpose of the whole guard.
            logger.debug("Labels: savepoint already closed.")
        return 0


def label_counts(db: Session) -> dict:
    """How much labelled data exists, by kind and class.

    The only reader, and it is a reporting helper rather than an engine input:
    the question "is there enough to train on yet" needs answering without
    writing a query by hand each time.
    """
    from sqlalchemy import func

    rows = db.execute(
        select(
            AssessmentLabel.kind,
            AssessmentLabel.accepted,
            func.count(AssessmentLabel.id),
        ).group_by(AssessmentLabel.kind, AssessmentLabel.accepted)
    ).all()
    out: dict = {}
    for kind, accepted, count in rows:
        bucket = out.setdefault(kind, {"accepted": 0, "rejected": 0})
        bucket["accepted" if accepted else "rejected"] += count
    return out


__all__ = [
    "KIND_NEW_RISK",
    "KIND_QUARTER_CHANGE",
    "KIND_RESOLVED_RISK",
    "label_counts",
    "quote_hash",
    "record_assessments",
]
