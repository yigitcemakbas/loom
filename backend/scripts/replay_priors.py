"""Score stored filings against the prior that existed when they arrived.

The fast path has produced one assessment in its entire existence. It is the
most interesting component in the engine, it needs no model and no network, and
it has never run enough times to be measured. The watcher can only fire when a
tracked company files while Loom happens to be watching, which on thirteen
armed companies is a handful of events a month.

This replays what is already stored instead. For each company that has a prior,
every document published AFTER that prior was generated is pushed through the
same `assess` call the watcher makes.

**The cutoff is the entire point.** An earlier validation attempt in this
project replayed filings that were part of the corpus the prior was built from,
which is circular: the prior already knew what the filing said, so of course it
matched. Only documents that postdate the prior are replayed here, so the
engine is being asked the question it would have faced live: given what Loom
believed on the twentieth, what does this filing from the twenty-fourth mean?

Assessments are marked as replayed in their external id, so the evaluation
harness can tell a reconstructed judgement from one made against a live feed.
They are not the same evidence: a replay has no latency, and latency is part of
what the fast path claims.

Usage:
    python -m scripts.replay_priors
    python -m scripts.replay_priors --dry-run NVDA
"""

import argparse
import logging
import sys
import time
from datetime import timezone

from sqlalchemy import select

from app.db.session import SessionLocal
from app.engine.exposure import dependents_of
from app.engine.reaction import MarketEvent, assess
from app.models.document import RawDocument
from app.repositories.assessment_repository import AssessmentRepository
from app.repositories.company_repository import CompanyRepository
from app.repositories.prior_repository import PriorRepository
from app.storage.blob_store import get_blob_store

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("replay_priors")

# Marks a reconstructed assessment so the evaluator can separate it from one
# made against a live feed. A replay has no latency, and latency is part of
# what the fast path claims, so the two are not interchangeable evidence.
REPLAY_PREFIX = "replay:"

# How much of a filing the keyword matcher sees. The whole body would be
# slower and no more accurate: watch-item keywords are short literal phrases,
# and a 10-K's exhibits are mostly boilerplate that matches nothing.
MAX_TEXT_CHARS = 200_000


def main() -> int:
    parser = argparse.ArgumentParser(description="Replay stored filings through the fast path.")
    parser.add_argument("tickers", nargs="*", help="Limit to these companies.")
    parser.add_argument("--dry-run", action="store_true", help="Score but write nothing.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        company_repo = CompanyRepository(db)
        prior_repo = PriorRepository(db)
        assessment_repo = AssessmentRepository(db)
        blob_store = get_blob_store()

        companies = (
            [c for c in (company_repo.get_by_ticker(t.upper()) for t in args.tickers) if c]
            if args.tickers else company_repo.list_all()
        )

        started = time.monotonic()
        scored = notable = written = skipped = 0

        for company in companies:
            prior = prior_repo.latest_for(company.id)
            if prior is None:
                continue

            # Strictly after the prior. A document the prior was built from
            # cannot test it.
            documents = list(db.execute(
                select(RawDocument)
                .where(RawDocument.company_id == company.id)
                .where(RawDocument.published_at > prior.generated_at)
                .order_by(RawDocument.published_at)
            ).scalars())
            if not documents:
                continue

            exposed = [
                {"ticker": e.ticker, "mention_count": e.mention_count}
                for e in dependents_of(db, company.id)
            ]

            company_notable = 0
            for document in documents:
                try:
                    body = blob_store.get(document.blob_uri).decode("utf-8", errors="ignore")
                except Exception:
                    skipped += 1
                    continue

                occurred = document.published_at
                if occurred is not None and occurred.tzinfo is None:
                    occurred = occurred.replace(tzinfo=timezone.utc)

                event = MarketEvent(
                    ticker=company.ticker,
                    kind="filing" if document.doc_subtype else "news",
                    occurred_at=occurred,
                    text=f"{document.doc_subtype or ''} {document.title or ''}\n{body[:MAX_TEXT_CHARS]}",
                    source_url=document.source_url,
                )
                result = assess(event, prior)
                scored += 1
                if result.is_notable:
                    notable += 1
                    company_notable += 1

                if args.dry_run:
                    continue

                created = assessment_repo.create(
                    company_id=company.id,
                    prior_id=prior.id,
                    external_id=f"{REPLAY_PREFIX}{document.id}",
                    kind=event.kind,
                    form=document.doc_subtype,
                    source_url=document.source_url,
                    score=result.score,
                    direction=result.direction,
                    headline=result.headline,
                    matches=[
                        {
                            "topic": m.topic,
                            "direction": m.direction,
                            "severity": m.severity,
                            "matched_keywords": m.matched_keywords,
                            "already_priced": m.already_priced,
                        }
                        for m in result.matches
                    ],
                    surprises={},
                    amplifiers=result.amplifiers,
                    exposed=exposed if result.is_notable else [],
                    occurred_at=occurred,
                    # Deliberately null. A replay has no latency, and writing a
                    # zero here would claim an instant reaction that never
                    # happened.
                    latency_seconds=None,
                    scoring_ms=result.elapsed_ms,
                )
                if created is not None:
                    written += 1

            if documents:
                logger.info(
                    "  %-6s %d documents after the prior, %d notable",
                    company.ticker, len(documents), company_notable,
                )

        if not args.dry_run:
            db.commit()

        logger.info(
            "\nScored %d filings, %d notable (%.0f%%), %d written, %d unreadable, in %.1fs.",
            scored, notable, (notable / scored * 100) if scored else 0.0,
            written, skipped, time.monotonic() - started,
        )
        if scored:
            logger.info(
                "Every one postdates the prior it was scored against, so none of "
                "them was in the corpus that built it."
            )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
