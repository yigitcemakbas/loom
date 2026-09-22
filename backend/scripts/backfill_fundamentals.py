"""Re-pull the full XBRL history for every company.

The fundamentals adapter stored four years because nothing read further back.
The factor backtest does, and four years capped it at fifty-five rebalances,
which is not enough to tell a real factor from a lucky one. The SEC's
companyfacts endpoint returns a filer's entire history in one payload and
always did; the old window only decided how much of that payload was discarded
after it arrived.

Facts dedupe on a content hash, so this is safe to re-run: existing rows are
recognised and skipped, and only the years that were previously dropped are
written.

Free, and paced by the shared SEC limiter.

Usage:
    python -m scripts.backfill_fundamentals
    python -m scripts.backfill_fundamentals NVDA AMD
"""

import argparse
import logging
import sys
import time

from app.db.session import SessionLocal
from app.ingestion.facts.sec_fundamentals import DEFAULT_YEARS, SecFundamentalsAdapter
from app.ingestion.registry import _persist_fact
from app.repositories.company_repository import CompanyRepository

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("backfill_fundamentals")


def main() -> int:
    parser = argparse.ArgumentParser(description="Re-pull full XBRL history.")
    parser.add_argument("tickers", nargs="*", help="Limit to these companies.")
    parser.add_argument("--years", type=int, default=DEFAULT_YEARS)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        repo = CompanyRepository(db)
        companies = (
            [c for c in (repo.get_by_ticker(t.upper()) for t in args.tickers) if c]
            if args.tickers else repo.list_all()
        )
        if not companies:
            logger.info("No companies to backfill.")
            return 0

        adapter = SecFundamentalsAdapter(years=args.years)
        logger.info("Pulling %d years of fundamentals for %d companies.", args.years, len(companies))

        started = time.monotonic()
        total_new = 0
        failed = 0

        for company in companies:
            try:
                dtos = adapter.fetch(company.ticker, since=None)
            except Exception:
                logger.exception("  %-6s fetch failed", company.ticker)
                failed += 1
                continue

            written = sum(1 for dto in dtos if _persist_fact(dto, company_id=company.id, db=db))
            # Committed per company so a long run keeps its progress.
            db.commit()
            total_new += written
            if written:
                logger.info("  %-6s %d new facts (%d seen)", company.ticker, written, len(dtos))

        logger.info(
            "Wrote %d new facts across %d companies in %.0fs (%d failed).",
            total_new, len(companies), time.monotonic() - started, failed,
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
