"""Bulk-seed the wide tier, so cross-sectional work has something to work with.

Eleven companies is enough to read about and far too few to rank. Every
cross-sectional technique worth the name (percentile a company against its
peers, neutralise a factor for sector, measure whether a signal ranks returns
at all) is undefined at that size. This script is how the universe gets to a
few hundred.

Tickers come from a file or the command line rather than from a scraped index
membership list, deliberately. Index lists live behind terms of use and change
without notice, and a wrong or stale universe is worse than a small one
because it quietly introduces survivorship bias into everything measured
afterwards. Supply the list you want and keep it under version control.

Names are resolved through the same SEC directory the ingestion adapters use,
so a ticker that cannot be resolved here would not have worked anyway, and is
reported rather than silently skipped.

Usage:
    python -m scripts.seed_universe --file universe.txt
    python -m scripts.seed_universe AAPL MSFT NVDA --tier focus
"""

import argparse
import logging
import sys

from app.db.session import SessionLocal
from app.models.company import CompanyTier
from app.repositories.company_repository import CompanyRepository
from app.repositories.watchlist_repository import WatchlistRepository
from app.schemas.company import CompanyCreate
from app.services.company_lookup import SecAccessDenied, get_company_lookup_service

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("seed_universe")


def _read_tickers(args) -> list[str]:
    tickers: list[str] = list(args.tickers)
    if args.file:
        with open(args.file) as handle:
            for line in handle:
                # Tolerate CSV exports: take the first column, skip comments.
                line = line.split("#", 1)[0].strip()
                if not line:
                    continue
                tickers.append(line.split(",")[0].strip())
    # Deduplicate while preserving the order given, so a run is reproducible.
    seen: set[str] = set()
    return [t.upper() for t in tickers if not (t.upper() in seen or seen.add(t.upper()))]


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed companies into the universe.")
    parser.add_argument("tickers", nargs="*", help="Tickers to add.")
    parser.add_argument("--file", help="File of tickers, one per line or first CSV column.")
    parser.add_argument(
        "--tier",
        choices=[t.value for t in CompanyTier],
        default=CompanyTier.WIDE.value,
        help="Coverage tier. Wide is numeric sources only and is the default.",
    )
    parser.add_argument(
        "--watchlist",
        action="store_true",
        help="Also add each company to the default watchlist so the scheduler picks it up.",
    )
    args = parser.parse_args()

    tickers = _read_tickers(args)
    if not tickers:
        parser.error("No tickers given. Pass them as arguments or via --file.")

    tier = CompanyTier(args.tier)
    lookup = get_company_lookup_service()
    db = SessionLocal()
    company_repo = CompanyRepository(db)

    added = existing = unresolved = 0
    failures: list[str] = []

    try:
        watchlist = WatchlistRepository(db).get_or_create_default() if args.watchlist else None

        for ticker in tickers:
            try:
                info = lookup.lookup(ticker)
            except SecAccessDenied as exc:
                # No point continuing: this fails identically for every ticker.
                logger.error("%s", exc)
                return 2

            if info is None:
                unresolved += 1
                failures.append(ticker)
                continue

            before = company_repo.get_by_ticker(ticker)
            company = company_repo.get_or_create(
                CompanyCreate(ticker=info.ticker, name=info.name, cik=info.cik),
                tier=tier,
            )
            if before is None:
                added += 1
            else:
                existing += 1

            if watchlist is not None:
                WatchlistRepository(db).add_company(watchlist.id, company.id)

        logger.info(
            "Seeded %d new, %d already present, %d unresolved, tier=%s.",
            added, existing, unresolved, tier.value,
        )
        if failures:
            logger.info("Unresolved tickers: %s", ", ".join(failures))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
