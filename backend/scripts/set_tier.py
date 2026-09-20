"""Move companies between coverage tiers.

The single lever that controls what this project costs to run: promoting a
company turns on documents, model analysis, and a standing prior for it;
demoting turns them off and leaves the numeric sources running.

Usage:
    python -m scripts.set_tier --tier focus NVDA AMD
    python -m scripts.set_tier --tier wide --all
"""

import argparse
import logging
import sys

from app.db.session import SessionLocal
from app.models.company import CompanyTier
from app.repositories.company_repository import CompanyRepository

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("set_tier")


def main() -> int:
    parser = argparse.ArgumentParser(description="Change a company's coverage tier.")
    parser.add_argument("tickers", nargs="*")
    parser.add_argument("--tier", choices=[t.value for t in CompanyTier], required=True)
    parser.add_argument("--all", action="store_true", help="Apply to every company.")
    args = parser.parse_args()

    if not args.tickers and not args.all:
        parser.error("Give tickers, or --all.")

    tier = CompanyTier(args.tier)
    db = SessionLocal()
    repo = CompanyRepository(db)
    try:
        targets = (
            [c.ticker for c in repo.list_all()] if args.all else [t.upper() for t in args.tickers]
        )
        changed = 0
        for ticker in targets:
            if repo.set_tier(ticker, tier) is None:
                logger.warning("Unknown ticker %s, skipped.", ticker)
                continue
            changed += 1
        logger.info("Moved %d companies to %s.", changed, tier.value)

        for t in CompanyTier:
            logger.info("  %s: %d", t.value, len(repo.list_by_tier(t)))
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
