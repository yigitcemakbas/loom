"""Fill in each company's sector from the SIC code it reports to the SEC.

The universe was seeded from a ticker list, which carries no sector, so all but
two companies had none. That was invisible until the factor engine ran its
first universe-wide pass and returned eight banks as the eight weakest
companies in the database, in order, having read nothing about any of them.
Ranking a bank against a software company on return on assets or leverage is
not a hard comparison, it is a meaningless one.

Free: the SIC code is in the submissions document the ingestion layer already
fetches, and the shared rate limiter paces the requests.

Usage:
    python -m scripts.backfill_sectors
    python -m scripts.backfill_sectors --force
"""

import argparse
import logging
import sys

import httpx

from app.config import settings
from app.db.session import SessionLocal
from app.engine.quant.sectors import sector_for_sic
from app.ingestion.rate_limit import limiter
from app.repositories.company_repository import CompanyRepository

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("backfill_sectors")

_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"


def main() -> int:
    parser = argparse.ArgumentParser(description="Fill company sectors from SEC SIC codes.")
    parser.add_argument("tickers", nargs="*", help="Limit to these companies.")
    parser.add_argument(
        "--force", action="store_true",
        help="Overwrite sectors that are already set. Default: only fill blanks.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        repo = CompanyRepository(db)
        companies = (
            [c for c in (repo.get_by_ticker(t.upper()) for t in args.tickers) if c]
            if args.tickers else repo.list_all()
        )
        pending = [
            c for c in companies
            if c.cik and (args.force or not c.sector)
        ]
        missing_cik = [c.ticker for c in companies if not c.cik]
        if missing_cik:
            logger.info("%d companies have no CIK and cannot be resolved: %s",
                        len(missing_cik), ", ".join(sorted(missing_cik)[:10]))
        if not pending:
            logger.info("Nothing to fill.")
            return 0

        headers = {"User-Agent": settings.sec_edgar_user_agent}
        filled = 0
        counts: dict[str, int] = {}

        with httpx.Client(headers=headers, timeout=30.0) as client:
            for company in pending:
                url = _SUBMISSIONS_URL.format(cik10=str(company.cik).zfill(10))
                limiter.acquire(url)
                try:
                    response = client.get(url)
                except httpx.HTTPError as exc:
                    logger.warning("  %-6s request failed: %s", company.ticker, exc)
                    continue
                if response.status_code != 200:
                    logger.warning("  %-6s submissions returned %s", company.ticker, response.status_code)
                    continue

                payload = response.json()
                sector = sector_for_sic(payload.get("sic"))
                if sector is None:
                    logger.info("  %-6s SIC %r maps to no sector", company.ticker, payload.get("sic"))
                    continue
                company.sector = sector
                counts[sector] = counts.get(sector, 0) + 1
                filled += 1

        db.commit()
        logger.info("Filled %d sectors.", filled)
        for sector, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            logger.info("  %-24s %d", sector, count)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
