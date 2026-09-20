"""Build or refresh standing priors across the focus tier.

The watcher can only recognise an event as significant if a prior says what
would be significant for that company. A focus company without one is watched
but mute: every filing scores zero and is indistinguishable from a quiet week.
This is the step that arms the fast path.

Usage:
    python -m scripts.build_priors
    python -m scripts.build_priors --stale-days 7
    python -m scripts.build_priors NVDA AMD
"""

import argparse
import logging
import sys
import time
from datetime import datetime, timedelta, timezone

from app.db.session import SessionLocal
from app.engine.llm_client import LLMUnavailableError
from app.engine.prior import build_prior
from app.models.company import CompanyTier
from app.repositories.company_repository import CompanyRepository
from app.repositories.prior_repository import PriorRepository

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger("build_priors")


def main() -> int:
    parser = argparse.ArgumentParser(description="Build standing priors for focus companies.")
    parser.add_argument("tickers", nargs="*", help="Specific tickers. Default: the whole focus tier.")
    parser.add_argument(
        "--stale-days",
        type=float,
        default=None,
        help="Only rebuild when the newest prior is older than this. Omit to rebuild all.",
    )
    args = parser.parse_args()

    db = SessionLocal()
    try:
        company_repo = CompanyRepository(db)
        prior_repo = PriorRepository(db)

        if args.tickers:
            companies = [c for c in (company_repo.get_by_ticker(t) for t in args.tickers) if c]
        else:
            companies = company_repo.list_by_tier(CompanyTier.FOCUS)

        if args.stale_days is not None:
            cutoff = datetime.now(timezone.utc) - timedelta(days=args.stale_days)
            fresh = []
            for company in companies:
                latest = prior_repo.latest_for(company.id)
                if latest is None or latest.generated_at < cutoff:
                    fresh.append(company)
            skipped = len(companies) - len(fresh)
            if skipped:
                logger.info("%d priors still fresh, skipping them.", skipped)
            companies = fresh

        if not companies:
            logger.info("Nothing to build.")
            return 0

        logger.info("Building priors for %d companies.", len(companies))
        built = failed = 0
        started = time.monotonic()

        for company in companies:
            try:
                prior = build_prior(company.ticker, db)
            except LLMUnavailableError as exc:
                # Applies equally to everything left, so stop rather than log
                # the same failure once per remaining company.
                logger.error("Stopping: %s", exc)
                break
            except Exception:
                logger.exception("Prior failed for %s", company.ticker)
                failed += 1
                continue

            if prior is None:
                logger.info("  %-6s no material to build from, skipped.", company.ticker)
                continue

            built += 1
            logger.info(
                "  %-6s %d watch items | %s",
                company.ticker, len(prior.watch_items or []), prior.summary[:88],
            )

        logger.info(
            "Built %d, failed %d, in %.0fs.", built, failed, time.monotonic() - started
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
