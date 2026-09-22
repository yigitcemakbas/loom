"""Score every company on Loom's quantitative factors.

Free and fast: the inputs are figures already in the database and the whole
pass is arithmetic, so this is the part of Loom that reaches the entire
universe rather than the handful of companies deep reading has covered.

Usage:
    python -m scripts.score_factors
    python -m scripts.score_factors --as-of 2026-06-30
    python -m scripts.score_factors --dry-run
"""

import argparse
import logging
import sys
import time
from datetime import date

from app.db.session import SessionLocal
from app.engine.quant.composite import build_composite, build_f_score
from app.engine.quant.runner import persist, score_universe

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("score_factors")


def main() -> int:
    parser = argparse.ArgumentParser(description="Compute quantitative factor scores.")
    parser.add_argument("tickers", nargs="*", help="Limit to these companies. Default: all.")
    parser.add_argument(
        "--as-of", type=date.fromisoformat, default=None,
        help="Score using only filings available on this date (YYYY-MM-DD). Default: today.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Compute and report, write nothing.")
    parser.add_argument("--top", type=int, default=10, help="How many companies to list each way.")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        started = time.monotonic()
        scores = score_universe(db, as_of=args.as_of, tickers=args.tickers or None)

        composites = {}
        for ticker, ranks in scores.ranked.items():
            composite = build_composite(ranks)
            if composite is not None:
                composites[ticker] = composite

        logger.info(
            "%d companies scored, %d with a composite, in %.1fs.",
            len(scores.raw), len(composites), time.monotonic() - started,
        )
        if scores.skipped_stale:
            logger.info("  %d skipped as stale: %s", len(scores.skipped_stale),
                        ", ".join(sorted(scores.skipped_stale)[:12]))
        if scores.skipped_thin:
            logger.info("  %d had no usable figures: %s", len(scores.skipped_thin),
                        ", ".join(sorted(scores.skipped_thin)[:12]))

        ordered = sorted(composites.items(), key=lambda kv: kv[1].score, reverse=True)
        logger.info("\nStrongest reported numbers:")
        for ticker, composite in ordered[: args.top]:
            health = build_f_score({k: v.value for k, v in scores.raw[ticker].items()})
            logger.info(
                "  %-6s %.3f  on %d measures, health %d/%d",
                ticker, composite.score, composite.factor_count, health.passed, health.available,
            )
        logger.info("\nWeakest reported numbers:")
        for ticker, composite in ordered[-args.top :][::-1]:
            health = build_f_score({k: v.value for k, v in scores.raw[ticker].items()})
            logger.info(
                "  %-6s %.3f  on %d measures, health %d/%d",
                ticker, composite.score, composite.factor_count, health.passed, health.available,
            )

        if args.dry_run:
            logger.info("\nDry run, nothing written.")
            return 0

        written = persist(db, scores)
        logger.info("\nWrote %d factor rows for %s.", written, scores.as_of)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
