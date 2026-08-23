"""Manually trigger ingestion for one ticker.

Usage: python -m scripts.ingest_once --ticker AAPL
"""

import argparse

from app.db.session import SessionLocal
from app.ingestion.registry import ingest_all


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", required=True)
    args = parser.parse_args()

    db = SessionLocal()
    try:
        results = ingest_all(args.ticker, db)
        total = sum(results.values())
        print(f"Ingested {total} new document(s) for {args.ticker}:")
        for source_name, count in results.items():
            print(f"  {source_name}: {count} new")
    finally:
        db.close()


if __name__ == "__main__":
    main()
