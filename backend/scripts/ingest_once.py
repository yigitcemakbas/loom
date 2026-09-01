"""Manually trigger ingestion for one or more tickers.

Usage:
    python -m scripts.ingest_once --ticker AAPL
    python -m scripts.ingest_once --ticker AAPL MSFT NVDA
    python -m scripts.ingest_once --watchlist
    python -m scripts.ingest_once --watchlist --workers 1
"""

import argparse
import logging
import time

from app.config import settings
from app.db.session import SessionLocal
from app.ingestion.registry import ingest_many
from app.repositories.watchlist_repository import WatchlistRepository


def _watchlist_tickers() -> list[str]:
    db = SessionLocal()
    try:
        repo = WatchlistRepository(db)
        watchlist = repo.get_or_create_default()
        return [company.ticker for company in repo.list_companies(watchlist.id)]
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--ticker", nargs="+", help="One or more tickers.")
    source.add_argument(
        "--watchlist", action="store_true", help="Every ticker on the default watchlist."
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=settings.ingest_max_workers,
        help="Tickers fetched at once. 1 forces sequential. Per-host request "
             "rates are capped independently of this, so a larger number "
             "overlaps waiting rather than asking anyone faster.",
    )
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
    )

    tickers = _watchlist_tickers() if args.watchlist else [t.upper() for t in args.ticker]
    if not tickers:
        print("Nothing to ingest: the watchlist is empty.")
        return

    started = time.monotonic()
    results = ingest_many(tickers, max_workers=args.workers)
    elapsed = time.monotonic() - started

    for result in results:
        if not result.ok:
            print(f"{result.ticker}: FAILED  {result.error}")
            continue
        detail = ", ".join(f"{name} {count}" for name, count in result.counts.items() if count)
        print(f"{result.ticker}: {result.total_new} new" + (f"  ({detail})" if detail else ""))

    total = sum(result.total_new for result in results)
    failed = sum(1 for result in results if not result.ok)
    print(
        f"\n{total} new item(s) across {len(results)} ticker(s) in {elapsed:.1f}s"
        + (f", {failed} failed." if failed else ".")
    )


if __name__ == "__main__":
    main()
