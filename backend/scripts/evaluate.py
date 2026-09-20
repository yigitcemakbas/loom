"""Run the measurement loop and print what it found.

Usage:
    python -m scripts.evaluate
    python -m scripts.evaluate --subject signals --horizons 1 2 5
"""

import argparse
import logging
import sys

from app.db.session import SessionLocal
from app.engine.evaluation import (
    BENCHMARK_TICKER,
    multiple_testing_threshold,
    survives_multiple_testing,
    DEFAULT_HORIZONS,
    evaluate_assessments,
    evaluate_signals,
)

logging.basicConfig(level=logging.WARNING, format="%(message)s")


def _print(report) -> None:
    print(f"\n=== {report.subject}, {report.horizon_sessions} session(s) forward "
          f"vs {BENCHMARK_TICKER} ===")
    print(f"  evaluated          {report.evaluated}  (skipped, no prices: {report.skipped_no_prices})")
    print(f"  directional calls  {report.directional}")
    if report.hit_rate is not None:
        print(f"  hit rate           {report.hit_rate * 100:.1f}%")
    if report.baseline_hit_rate is not None:
        print(f"  naive baseline     {report.baseline_hit_rate * 100:.1f}%  "
              f"(always call the period's dominant direction)")
    if report.edge_over_baseline is not None:
        print(f"  edge over baseline {report.edge_over_baseline * 100:+.1f} points")
    for label, value in (
        ("mean abnormal, bullish", report.mean_abnormal_positive),
        ("mean abnormal, bearish", report.mean_abnormal_negative),
        ("mean abnormal, neutral", report.mean_abnormal_neutral),
    ):
        if value is not None:
            print(f"  {label:22} {value:+.3f}%")
    if report.spread is not None:
        print(f"  spread (bull - bear)   {report.spread:+.3f}%")
    if report.t_statistic is not None:
        print(f"  t statistic            {report.t_statistic:+.2f}")
    if report.information_coefficient is not None:
        print(f"  information coefficient {report.information_coefficient:+.4f}")
    print(f"\n  {report.verdict()}")


def main() -> int:
    parser = argparse.ArgumentParser(description="Measure whether Loom's judgements predicted anything.")
    parser.add_argument("--subject", choices=["signals", "assessments", "both"], default="both")
    parser.add_argument("--horizons", nargs="*", type=int, default=list(DEFAULT_HORIZONS))
    args = parser.parse_args()

    db = SessionLocal()
    try:
        reports = []
        for horizon in args.horizons:
            if args.subject in ("signals", "both"):
                report = evaluate_signals(db, horizon=horizon)
                reports.append(report)
                _print(report)
            if args.subject in ("assessments", "both"):
                report = evaluate_assessments(db, horizon=horizon)
                reports.append(report)
                _print(report)

        if len(reports) > 1:
            threshold = multiple_testing_threshold(len(reports))
            survivors = survives_multiple_testing(reports)
            print(f"\n=== multiple testing ===")
            print(f"  {len(reports)} variants tested, so |t| must clear {threshold:.2f}, not 2.00.")
            print(f"  Picking the best of several horizons without this correction is the"
                  f"\n  most reliable way to turn noise into a discovery.")
            print(f"  Survivors: {', '.join(survivors) if survivors else 'none'}")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(main())
