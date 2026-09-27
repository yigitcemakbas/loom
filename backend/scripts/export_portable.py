"""Export the part of Loom that cannot be rebuilt from public sources.

Moving Loom to a host does not mean moving Loom's database. Almost all of it is
a cache of things that are free to fetch again: price bars come back from the
price source in about thirteen minutes for a thousand companies, fundamentals
come back from SEC's XBRL endpoints, the search index is derived from documents
already stored, and factor scores are arithmetic over both. Measured on a real
database, that is 617 MB of the 625 MB total.

The remaining 8 MB is the part that would genuinely be lost:

  * **findings, priors and the analysis record**, which cost model calls. This
    is the only irreplaceable thing in the system in the ordinary sense: the
    quota spent on them is gone and re-reading two hundred documents would
    spend it again.
  * **accounts, sessions and positions**, which are the user's, not Loom's.
  * **agent decisions**, which are the recorded results of an experiment and
    are not reproducible by definition: re-running the trial produces a new
    trial, not the same one.
  * **companies**, which are cheap to fetch again but whose primary keys anchor
    every foreign key above. Re-seeding them would produce new identifiers and
    orphan every finding.
  * **raw documents**, small, and the text that every finding's evidence quote
    was verified against.

So this exports those and leaves the rest behind, which is what makes the move
possible on a machine with no disk to spare: the dump is a few megabytes rather
than a few hundred, and the large tables are rebuilt on the destination where
there is room for them.

Connects over the published port rather than through the container runtime, so
it still works when the Docker CLI is wedged, which on a full disk it will be.

Usage:
    python -m scripts.export_portable
    python -m scripts.export_portable --dsn postgresql://loom:loom@localhost:5432/loom --out loom.portable.gz
"""

import argparse
import gzip
import logging
import sys
from pathlib import Path

import psycopg

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("export_portable")

DEFAULT_DSN = "postgresql://loom:loom@localhost:5432/loom"

# Dependency order. Parents first, because the import replays this list
# forwards and a child row whose parent is missing is rejected by the
# constraint that exists to reject it.
PORTABLE_TABLES: tuple[str, ...] = (
    "companies",
    "watchlists",
    "watchlist_items",
    "raw_documents",
    "document_analyses",
    "signals",
    "company_priors",
    "company_briefs",
    "event_assessments",
    "users",
    "user_sessions",
    "login_codes",
    "positions",
    "agent_decisions",
)

# A marker the import splits on. Chosen to be something no COPY payload can
# contain: COPY text format escapes newlines inside values, so a line can never
# begin with this by accident.
MARKER = "\\\\LOOM-TABLE\t"


def main() -> int:
    parser = argparse.ArgumentParser(description="Export Loom's irreplaceable data.")
    parser.add_argument("--dsn", default=DEFAULT_DSN, help="Source database.")
    parser.add_argument("--out", default="loom.portable.gz", help="Output file.")
    args = parser.parse_args()

    out = Path(args.out)
    written: dict[str, int] = {}

    try:
        with psycopg.connect(args.dsn, connect_timeout=15) as conn:
            existing = {
                row[0] for row in conn.execute(
                    "SELECT tablename FROM pg_tables WHERE schemaname='public'"
                )
            }
            with gzip.open(out, "wt", encoding="utf-8", newline="") as handle:
                for table in PORTABLE_TABLES:
                    if table not in existing:
                        # Named rather than skipped silently. A table that has
                        # been renamed since this list was written would
                        # otherwise vanish from the backup without comment.
                        logger.warning("  %-22s not present, skipped", table)
                        continue
                    handle.write(f"{MARKER}{table}\n")
                    rows = 0
                    with conn.cursor().copy(
                        f"COPY {table} TO STDOUT (FORMAT text)"
                    ) as copy:
                        for line in copy:
                            handle.write(bytes(line).decode("utf-8"))
                            rows += 1
                    written[table] = rows
                    logger.info("  %-22s %7d rows", table, rows)
    except Exception:
        logger.exception("Export failed; the output file may be incomplete")
        return 1

    size = out.stat().st_size
    logger.info(
        "\nWrote %s (%.1f MB compressed), %d rows across %d tables.",
        out, size / 1048576, sum(written.values()), len(written),
    )
    logger.info(
        "Everything not listed above rebuilds on the destination:\n"
        "  alembic upgrade head\n"
        "  python -m scripts.import_portable --in %s\n"
        "  python -m scripts.ingest_prices --years 5\n"
        "  python -m scripts.backfill_fundamentals\n"
        "  python -m scripts.score_factors",
        out.name,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
