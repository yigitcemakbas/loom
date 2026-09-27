"""Seed the universe from SEC's own directory of filers.

Eleven companies is enough to read about and far too few to rank; a hundred and
thirty is enough to rank badly. Every cross-sectional measurement Loom makes is
a statement about a peer group, and at this size most of those groups do not
exist: of the nine sectors in the database, exactly one has enough members to
compute a percentile anyone should trust, which is why the sector-level work
kept coming back measuring Technology and calling it the market.

**Why this source.** `seed_universe.py` deliberately refuses scraped index
membership lists, and the reason it gives is right: they sit behind terms of
use and change without notice. This is not one of those. `company_tickers_
exchange.json` is SEC's own directory of everyone who files with it, published
for exactly this purpose, and it carries the CIK and the exchange, so seeding
from it costs a single request for the whole universe rather than one request
per company.

It is also, verifiably, ordered by market capitalisation descending: the file
opens NVDA, AAPL, GOOGL, MSFT, AMZN and is into micro-caps by row three
thousand. That makes `--limit N` a reproducible, documented selection rather
than an arbitrary slice, which matters because the number is going to move
around as disk allows.

**What this does not fix, and must not be read as fixing.** Ordering by today's
market capitalisation selects for companies that exist and are large today. For
describing the market now, and for measuring anything forward, that is the
right universe. For backtesting it is contaminated, and expanding it this way
makes the contamination larger rather than smaller: a longer list of today's
winners is still a list of today's winners. The factor backtest already carries
this problem, diagnosed against its one surviving result, and nothing here
changes that.

Everything is seeded to the WIDE tier, which is numeric sources only and costs
no model calls. Depth is bought separately and deliberately.

Usage:
    python -m scripts.seed_from_sec --limit 1000
    python -m scripts.seed_from_sec --limit 500 --exchange NYSE
"""

import argparse
import json
import logging
import sys
import urllib.request

from app.config import get_settings
from app.db.session import SessionLocal
from app.models.company import CompanyTier
from app.repositories.company_repository import CompanyRepository
from app.schemas.company import CompanyCreate

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("seed_from_sec")

DIRECTORY_URL = "https://www.sec.gov/files/company_tickers_exchange.json"

# Exchanges worth ranking against each other. OTC is excluded deliberately:
# those names trade thinly enough that a daily close is often a stale quote
# rather than a price, and a peer group built from them would produce
# percentiles that describe liquidity rather than the businesses.
DEFAULT_EXCHANGES = ("Nasdaq", "NYSE")

# Tickers carrying share-class and warrant suffixes. A company's B shares are
# not a second company, and ranking both puts one business in a peer group
# twice; warrants and units are not the equity at all.
_SKIP_SUFFIXES = ("-W", "-WS", "-U", "-R", "-RT", "-P")


def _usable(ticker: str) -> bool:
    if not ticker or len(ticker) > 6:
        return False
    return not any(ticker.endswith(suffix) for suffix in _SKIP_SUFFIXES)


def fetch_directory(user_agent: str) -> list[dict]:
    """SEC's whole filer directory, in its own order.

    One request for the entire universe. The User-Agent is the same one the
    ingestion adapters send, and it matters: measured against sec.gov, the
    header must contain an email-shaped token and must not contain a URL, or
    every request comes back 403.
    """
    request = urllib.request.Request(DIRECTORY_URL, headers={"User-Agent": user_agent})
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.load(response)

    fields = payload["fields"]
    return [dict(zip(fields, row)) for row in payload["data"]]


def main() -> int:
    parser = argparse.ArgumentParser(description="Seed companies from SEC's filer directory.")
    parser.add_argument(
        "--limit", type=int, default=1000,
        help="How many of the largest filers to seed. The directory is ordered by size.",
    )
    parser.add_argument(
        "--exchange", action="append", choices=["Nasdaq", "NYSE", "CBOE", "OTC"],
        help="Exchanges to include. Repeatable. Default: Nasdaq and NYSE.",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Report what would be seeded without writing anything.",
    )
    args = parser.parse_args()

    exchanges = set(args.exchange or DEFAULT_EXCHANGES)
    settings = get_settings()

    try:
        directory = fetch_directory(settings.sec_edgar_user_agent)
    except Exception:
        logger.exception("Could not read SEC's filer directory")
        return 1

    wanted: list[dict] = []
    for position, row in enumerate(directory):
        if row.get("exchange") not in exchanges:
            continue
        ticker = (row.get("ticker") or "").upper()
        if not _usable(ticker):
            continue
        # The position in the *unfiltered* directory, so the rank stays a size
        # proxy rather than a position in whatever subset was requested. Two
        # runs with different exchange filters then agree about which company
        # is larger.
        wanted.append({**row, "sec_rank": position})
        if len(wanted) >= args.limit:
            break

    logger.info(
        "SEC lists %d filers; %d on %s after filtering, seeding the largest %d.",
        len(directory), len(wanted), " and ".join(sorted(exchanges)), len(wanted),
    )

    if args.dry_run:
        logger.info("Dry run. First 10: %s", ", ".join(r["ticker"] for r in wanted[:10]))
        return 0

    db = SessionLocal()
    try:
        repo = CompanyRepository(db)
        created = existing = 0
        for row in wanted:
            ticker = row["ticker"].upper()
            found = repo.get_by_ticker(ticker)
            if found is not None:
                # Fill what a company seeded before this script existed is
                # missing, which is most of the original universe. Only blanks
                # are filled: a sector resolved from a SIC code or an exchange
                # corrected by hand is better than anything here, and
                # overwriting it would undo that work on every run.
                if found.sec_rank is None:
                    found.sec_rank = row["sec_rank"]
                if not found.exchange and row.get("exchange"):
                    found.exchange = row["exchange"]
                existing += 1
                continue
            repo.create(
                CompanyCreate(
                    ticker=ticker,
                    name=row["name"],
                    # Zero-padded to ten digits, which is the form every SEC
                    # endpoint expects and the form the adapters already build.
                    cik=str(row["cik"]).zfill(10),
                    exchange=row.get("exchange"),
                    sec_rank=row["sec_rank"],
                ),
                tier=CompanyTier.WIDE,
            )
            created += 1
            if created % 100 == 0:
                db.commit()
                logger.info("  %d seeded...", created)
        db.commit()
        logger.info("Seeded %d new companies, %d already present.", created, existing)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
