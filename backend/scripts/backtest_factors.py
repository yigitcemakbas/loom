"""Measure whether Loom's factors ranked returns.

The first test in this project with a sample large enough to mean something:
roughly sixty rebalances of a hundred and twenty companies, against the
forty-five directional calls the signal evaluator has to work with.

Costs nothing. Every input is already stored, the factor engine already
reconstructs what was knowable on any past date, and no model is called.

Usage:
    python -m scripts.backtest_factors
    python -m scripts.backtest_factors --start 2022-01-01 --step-days 60
"""

import argparse
import logging
import sys
import time
from datetime import date, timedelta

from app.db.session import SessionLocal
from app.engine.evaluation import multiple_testing_threshold
from app.engine.quant.backtest import REBALANCE_DAYS, run_backtest

logging.basicConfig(level=logging.INFO, format="%(message)s")
# The universe scorer logs a line per rebalance, which would bury the result.
logging.getLogger("app.engine.quant.runner").setLevel(logging.WARNING)
logger = logging.getLogger("backtest")


def _pct(value: float | None, places: int = 2) -> str:
    return "-" if value is None else f"{value * 100:+.{places}f}%"


def main() -> int:
    parser = argparse.ArgumentParser(description="Backtest the quantitative factors.")
    parser.add_argument("--start", type=date.fromisoformat, default=None,
                        help="First formation date. Default: five years ago.")
    parser.add_argument("--end", type=date.fromisoformat, default=None,
                        help="Last date considered. Default: today.")
    parser.add_argument("--step-days", type=int, default=REBALANCE_DAYS,
                        help="Rebalance cadence, which is also the holding period.")
    parser.add_argument("--quantiles", type=int, default=5,
                        help="How many buckets to sort into. 5 = quintiles.")
    args = parser.parse_args()

    end = args.end or date.today()
    start = args.start or (end - timedelta(days=365 * 5))

    db = SessionLocal()
    try:
        started = time.monotonic()
        result = run_backtest(
            db, start=start, end=end,
            step_days=args.step_days, quantiles=args.quantiles,
        )

        survivorship = result.survivorship
        logger.info(
            "\n%d rebalances from %s to %s, %d companies, %d day holding period, %.0fs.",
            result.rebalances, result.start, result.end,
            survivorship.companies, args.step_days, time.monotonic() - started,
        )

        # Printed before the results, deliberately. Every long-only number
        # below is contaminated by this and a reader should know by how much
        # before seeing any of them.
        logger.info("\n=== survivorship ===")
        logger.info("  The universe is today's index members, so every company in it")
        logger.info("  survived. Nothing delisted, acquired at a discount or taken to zero")
        logger.info("  is present, and a company added in 2024 because it had done well is")
        logger.info("  in the 2021 backtest. This cannot be repaired without a historical")
        logger.info("  membership list. It can be measured:")
        logger.info("    universe mean return   %s per period", _pct(survivorship.mean_universe_return))
        logger.info("    benchmark mean return  %s per period", _pct(survivorship.mean_benchmark_return))
        logger.info("    free lunch             %s per period", _pct(survivorship.excess))
        logger.info("    late entrants          %d of %d companies had no price at the start",
                    survivorship.late_entrants, survivorship.companies)
        logger.info("  The long-short SPREAD is largely immune: both legs are drawn from")
        logger.info("  the same survivors, so the bias mostly cancels. Long-only excess is")
        logger.info("  not immune and is marked contaminated wherever it appears.")

        ordered = sorted(
            result.factors,
            key=lambda f: abs(f.t_statistic or 0.0), reverse=True,
        )

        logger.info("\n=== factor spreads (top quintile minus bottom, per period) ===")
        logger.info(
            "  %-24s %3s %8s %8s %6s %7s %9s",
            "factor", "n", "spread", "t", "hit", "IC", "long exc*",
        )
        for factor in ordered:
            logger.info(
                "  %-24s %3d %8s %8s %6s %7s %9s",
                factor.label[:24],
                len(factor.months),
                _pct(factor.mean_spread),
                "-" if factor.t_statistic is None else f"{factor.t_statistic:+.2f}",
                "-" if factor.hit_rate is None else f"{factor.hit_rate * 100:.0f}%",
                "-" if factor.mean_information_coefficient is None
                else f"{factor.mean_information_coefficient:+.3f}",
                _pct(factor.mean_long_excess),
            )
        logger.info("  * long excess is survivorship-contaminated; the spread is not.")

        trials = len(result.factors)
        threshold = multiple_testing_threshold(trials)
        survivors = [
            f for f in result.factors
            if f.t_statistic is not None and abs(f.t_statistic) >= threshold
        ]

        logger.info("\n=== multiple testing ===")
        logger.info("  %d factors tested, so |t| must clear %.2f, not 2.00.", trials, threshold)
        if survivors:
            for factor in survivors:
                logger.info(
                    "  SURVIVES: %s, spread %s, t=%+.2f over %d periods.",
                    factor.label, _pct(factor.mean_spread),
                    factor.t_statistic, len(factor.months),
                )
            logger.info("\n  A surviving spread is a reason to keep looking, not a strategy.")
            logger.info("  There are no transaction costs, borrow costs or slippage here, and")
            logger.info("  the short leg of a quintile spread is the expensive half to trade.")
        else:
            logger.info("  Survivors: none.")
            logger.info("\n  No factor ranked returns in this universe over this window by")
            logger.info("  enough to distinguish from chance. That is a real finding about")
            logger.info("  a small, survivor-biased, large-cap universe over a short window,")
            logger.info("  not a verdict on the factors themselves.")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
