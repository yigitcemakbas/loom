"""Regenerate every stored brief from the findings currently in the database.

A brief is a snapshot, not a view: it is computed when a company is ingested
and then served as-is. That is the right default, because the headline output
should not depend on a model provider being up at read time, but it means any
change to the findings underneath a brief is invisible until the brief is
rebuilt. Backfilling market-reaction tags is exactly such a change, and so is
editing the stance thresholds in engine/brief.py.

Free to run: build_brief is arithmetic over stored signals and makes no model
call and no network request, so this can be run as often as the evidence
changes.

Usage:
    python -m scripts.rebuild_briefs
    python -m scripts.rebuild_briefs NVDA GOOGL
"""

import argparse
import logging
import sys
import time

from app.db.session import SessionLocal
from app.engine.pipeline import regenerate_brief
from app.repositories.company_repository import CompanyRepository

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("rebuild_briefs")


def main() -> int:
    parser = argparse.ArgumentParser(description="Recompute stored briefs from current findings.")
    parser.add_argument("tickers", nargs="*", help="Specific tickers. Default: every company.")
    parser.add_argument(
        "--changed-only", action="store_true",
        help="Only log companies whose stance moved, rather than every company.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        repo = CompanyRepository(db)
        if args.tickers:
            companies = [c for c in (repo.get_by_ticker(t.upper()) for t in args.tickers) if c]
        else:
            companies = repo.list_all()

        if not companies:
            logger.info("No companies to rebuild.")
            return 0

        logger.info("Rebuilding briefs for %d companies.", len(companies))
        started = time.monotonic()
        from app.repositories.brief_repository import BriefRepository

        brief_repo = BriefRepository(db)
        changed = rebuilt = 0

        for company in companies:
            before = brief_repo.latest_for(company.id)
            previous_stance = before.stance if before else None
            try:
                stored = regenerate_brief(company.ticker, db)
            except Exception:
                logger.exception("Brief failed for %s", company.ticker)
                continue
            if stored is None:
                continue
            rebuilt += 1
            moved = previous_stance is not None and stored.stance != previous_stance
            if moved:
                changed += 1
                logger.info(
                    "  %-6s %s -> %s  (%d findings)",
                    company.ticker,
                    getattr(previous_stance, "value", previous_stance),
                    getattr(stored.stance, "value", stored.stance),
                    stored.signal_count,
                )
            elif not args.changed_only:
                logger.info(
                    "  %-6s %s  (%d findings)",
                    company.ticker,
                    getattr(stored.stance, "value", stored.stance),
                    stored.signal_count,
                )

        db.commit()
        logger.info(
            "Rebuilt %d briefs in %.0fs, %d changed stance.",
            rebuilt, time.monotonic() - started, changed,
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
