"""Load a portable export into a fresh Loom database.

The other half of `export_portable`. Run after `alembic upgrade head` has
created the schema and before any of the rebuild scripts, because those derive
their work from the companies and documents this restores.

Refuses to write into a table that already has rows unless told to replace,
which is the difference between a restore and a silent doubling. Loom's primary
keys are UUIDs rather than sequences, so nothing needs resetting afterwards and
a re-run cannot collide with itself.

Usage:
    python -m scripts.import_portable --in loom.portable.gz
    python -m scripts.import_portable --in loom.portable.gz --replace
"""

import argparse
import gzip
import logging
import sys
from pathlib import Path

import psycopg

from scripts.export_portable import DEFAULT_DSN, MARKER, PORTABLE_TABLES

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("import_portable")


def _blocks(path: Path):
    """Yield (table, lines) in the order the export wrote them."""
    table: str | None = None
    lines: list[str] = []
    with gzip.open(path, "rt", encoding="utf-8", newline="") as handle:
        for line in handle:
            if line.startswith(MARKER):
                if table is not None:
                    yield table, lines
                table = line[len(MARKER):].strip()
                lines = []
            elif table is not None:
                lines.append(line)
    if table is not None:
        yield table, lines


def main() -> int:
    parser = argparse.ArgumentParser(description="Restore Loom's irreplaceable data.")
    parser.add_argument("--dsn", default=DEFAULT_DSN, help="Destination database.")
    parser.add_argument("--in", dest="source", default="loom.portable.gz", help="Export file.")
    parser.add_argument(
        "--replace", action="store_true",
        help="Empty each table before loading it. Without this, a table that "
             "already has rows is left alone and reported.",
    )
    args = parser.parse_args()

    path = Path(args.source)
    if not path.exists():
        logger.error("No such file: %s", path)
        return 1

    blocks = list(_blocks(path))
    # Replayed in the order the export wrote them, which is parents first. A
    # child row whose parent has not been loaded is rejected by the constraint
    # that exists to reject it, so the ordering is load-bearing rather than
    # tidiness.
    order = {name: index for index, name in enumerate(PORTABLE_TABLES)}
    blocks.sort(key=lambda item: order.get(item[0], len(order)))

    try:
        with psycopg.connect(args.dsn, connect_timeout=15) as conn:
            loaded = skipped = 0
            for table, lines in blocks:
                count = conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                if count and not args.replace:
                    logger.warning("  %-22s already has %d rows, left alone", table, count)
                    skipped += 1
                    continue
                if count:
                    # CASCADE because these tables reference each other and
                    # emptying a parent alone would be refused.
                    conn.execute(f"TRUNCATE {table} CASCADE")
                if not lines:
                    logger.info("  %-22s %7d rows", table, 0)
                    continue
                with conn.cursor().copy(f"COPY {table} FROM STDIN (FORMAT text)") as copy:
                    for line in lines:
                        copy.write(line)
                logger.info("  %-22s %7d rows", table, len(lines))
                loaded += len(lines)
            conn.commit()
    except Exception:
        logger.exception("Import failed; nothing was committed")
        return 1

    logger.info("\nLoaded %d rows. %d tables were left alone.", loaded, skipped)
    logger.info(
        "Now rebuild what was deliberately left behind:\n"
        "  python -m scripts.ingest_prices --years 5\n"
        "  python -m scripts.backfill_fundamentals\n"
        "  python -m scripts.score_factors"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
