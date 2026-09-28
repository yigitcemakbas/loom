"""Deepen the periodic-filing corpus across the universe.

Free: no model quota, only SEC's shared rate limit. Idempotent and resumable —
a filing already stored is recognised from the submissions index and never
downloaded, so re-running costs one index request per company.

Usage:
    python -m scripts.backfill_filings
    python -m scripts.backfill_filings --target 4 --batch 200
    python -m scripts.backfill_filings NVDA AMD
"""

import argparse
import logging
import sys
import time

from sqlalchemy import select

from app.db.session import SessionLocal
from app.engine.filings import (
    TARGET_FILINGS,
    backfill_filings,
    companies_needing_filings,
    filing_counts,
)
from app.models.company import Company

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("backfill_filings")


def main() -> int:
    parser = argparse.ArgumentParser(description="Store annual and quarterly reports.")
    parser.add_argument("tickers", nargs="*", help="Limit to these companies.")
    parser.add_argument("--target", type=int, default=TARGET_FILINGS,
                        help="Filings each company should hold.")
    parser.add_argument("--batch", type=int, default=150,
                        help="Companies per pass. The pass repeats until done.")
    args = parser.parse_args()

    db = SessionLocal()
    if args.tickers:
        wanted = [t.upper() for t in args.tickers]
        companies = list(db.execute(
            select(Company).where(Company.ticker.in_(wanted))
        ).scalars())
        result = backfill_filings(db, fetch_limit=len(companies) * args.target,
                                 company_limit=len(companies), target=args.target,
                                 companies=companies)
        logger.info("%s", result)
        return 0

    before = filing_counts(db)
    total_before = sum(before.values())
    started = time.time()
    stored = passes = 0

    # Repeated bounded passes rather than one unbounded sweep, so the run can be
    # interrupted at any point without leaving a company half fetched, and so a
    # failure in one batch does not lose the ones before it.
    while True:
        outstanding = companies_needing_filings(db, target=args.target)
        if not outstanding:
            break
        result = backfill_filings(
            db,
            fetch_limit=args.batch * args.target,
            company_limit=args.batch,
            target=args.target,
            refresh_share=0,
        )
        passes += 1
        stored += result.filings_stored
        logger.info("pass %d: %s", passes, result)
        if result.filings_stored == 0 and result.companies_examined == 0:
            break
        if result.filings_stored == 0 and result.already_held >= result.companies_examined:
            # Everything this pass looked at is either complete or has nothing
            # more to give. Without this a universe of foreign filers would loop.
            logger.info("nothing further available for the remaining companies.")
            break

    after = filing_counts(db)
    logger.info(
        "=== stored %d filings in %.0fs: %d -> %d across %d companies ===",
        stored, time.time() - started, total_before, sum(after.values()), len(after),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
