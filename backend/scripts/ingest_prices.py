"""Store daily price history for the universe.

Prices were previously fetched for a chart and discarded, which is why Loom
could rank a company's quality and never say whether that quality was already
paid for. Valuation is a ratio between a company's figures and its price;
without a stored price there is no denominator.

Idempotent and incremental: sessions already stored are left alone, so a
re-run fetches only what is new. Re-running daily after the close is the
intended cadence.

Usage:
    python -m scripts.ingest_prices
    python -m scripts.ingest_prices --years 10 NVDA AMD
"""

import argparse
import logging
import sys
import time

from sqlalchemy import select

from app.db.session import SessionLocal
from app.ingestion.prices import get_price_source
from app.models.price_bar import PriceBar
from app.repositories.company_repository import CompanyRepository

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("ingest_prices")

# The provider is undocumented and unofficial. Pausing between tickers is the
# difference between a backfill that completes and one that gets rate limited
# halfway through and leaves the universe half priced, which would quietly
# shrink the peer group for every valuation factor.
PAUSE_SECONDS = 0.4


def main() -> int:
    parser = argparse.ArgumentParser(description="Store daily closes for the universe.")
    parser.add_argument("tickers", nargs="*", help="Limit to these companies. Default: all.")
    parser.add_argument("--years", type=int, default=6, help="How far back to request.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        repo = CompanyRepository(db)
        companies = (
            [c for c in (repo.get_by_ticker(t.upper()) for t in args.tickers) if c]
            if args.tickers else repo.list_all()
        )
        if not companies:
            logger.info("No companies to price.")
            return 0

        source = get_price_source()
        logger.info("Fetching %d years of daily closes for %d companies.", args.years, len(companies))

        started = time.monotonic()
        total_new = 0
        covered = empty = 0

        for index, company in enumerate(companies):
            existing = set(
                db.execute(
                    select(PriceBar.session_date).where(PriceBar.company_id == company.id)
                ).scalars()
            )
            bars = source.daily_history(company.ticker, years=args.years)
            if not bars:
                empty += 1
                logger.info("  %-6s no price history available", company.ticker)
                time.sleep(PAUSE_SECONDS)
                continue

            fresh = [b for b in bars if b.session_date not in existing]
            for bar in fresh:
                db.add(
                    PriceBar(
                        company_id=company.id,
                        session_date=bar.session_date,
                        close=bar.close,
                        adjusted_close=bar.adjusted_close,
                        volume=bar.volume,
                    )
                )
            # Committed per company so a rate limit partway through keeps
            # everything fetched so far.
            db.commit()
            total_new += len(fresh)
            covered += 1
            if fresh:
                logger.info(
                    "  %-6s %d new sessions (%s to %s)",
                    company.ticker, len(fresh), fresh[0].session_date, fresh[-1].session_date,
                )
            if index + 1 < len(companies):
                time.sleep(PAUSE_SECONDS)

        logger.info(
            "Stored %d new sessions across %d companies in %.0fs (%d had none available).",
            total_new, covered, time.monotonic() - started, empty,
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
