"""Score Loom's own verdicts, by component and horizon.

The signal evaluator scores individual findings and the factor backtest scores
individual measures. Neither has ever scored the thing the product actually
shows a person, and Loom has been issuing dated verdicts since August.

Split by component on purpose. "Is Loom right" answered as one number is the
least useful shape the answer could take: a null says nothing about what to
fix and a positive says nothing about what to keep.

Usage:
    python -m scripts.evaluate_verdicts
    python -m scripts.evaluate_verdicts --horizons 1 5 21 63
"""

import argparse
import logging
import sys
import time

from app.db.session import SessionLocal
from app.engine.verdicts import HORIZONS, evaluate_verdicts

logging.basicConfig(level=logging.INFO, format="%(message)s")
logging.getLogger("app.engine.quant.runner").setLevel(logging.WARNING)
logger = logging.getLogger("evaluate_verdicts")

_LABEL = {
    "read": "what Loom read",
    "numbers": "what the accounts say",
}


def main() -> int:
    parser = argparse.ArgumentParser(description="Score Loom's verdicts.")
    parser.add_argument("--horizons", type=int, nargs="+", default=list(HORIZONS))
    args = parser.parse_args()

    db = SessionLocal()
    try:
        started = time.monotonic()
        report = evaluate_verdicts(db, horizons=args.horizons)

        logger.info("\nScored in %.0fs.\n", time.monotonic() - started)
        logger.info(
            "  %-22s %6s %7s %8s %8s %9s %8s",
            "component", "sess", "calls", "hit", "spread", "t", "IC",
        )
        for result in report.results:
            logger.info(
                "  %-22s %6d %7d %7s %8s %9s %8s",
                _LABEL.get(result.component, result.component),
                result.horizon, result.calls,
                "-" if result.hit_rate is None else f"{result.hit_rate * 100:.0f}%",
                "-" if result.spread is None else f"{result.spread:+.2f}%",
                "-" if result.t_statistic is None else f"{result.t_statistic:+.2f}",
                "-" if result.information_coefficient is None
                else f"{result.information_coefficient:+.3f}",
            )
            if result.skipped:
                logger.info("  %-22s %s", "", f"({result.skipped} skipped, no price window yet)")

        logger.info("\n=== multiple testing ===")
        logger.info(
            "  %d component/horizon pairs tested, so |t| must clear %.2f.",
            len(report.results), report.threshold,
        )
        survivors = report.survivors()
        if survivors:
            for s in survivors:
                logger.info(
                    "  SURVIVES: %s at %d session(s), spread %+.2f%%, t=%+.2f over %d calls.",
                    _LABEL.get(s.component, s.component), s.horizon,
                    s.spread or 0, s.t_statistic or 0, s.calls,
                )
        else:
            logger.info("  Survivors: none.")

        logger.info("\n=== what each says ===")
        for result in report.results:
            logger.info(
                "  %s, %d session(s): %s",
                _LABEL.get(result.component, result.component),
                result.horizon, result.verdict(),
            )

        logger.info(
            "\n  Verdicts are one per company per day, entered strictly after they "
            "\n  were issued, and measured against %s. The two components are scored "
            "\n  apart because a null on one and a signal on the other is actionable, "
            "\n  while a blended number is not.",
            "QQQ",
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
