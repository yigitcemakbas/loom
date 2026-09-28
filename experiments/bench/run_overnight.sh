#!/bin/zsh
# The overnight chain. Each stage logs separately and a stage that fails does
# not take the rest of the night with it: the analysis reads whatever the score
# build managed to cache, and the reader crawl is independent of both.
set -u
BENCH=/Users/tugayakbas/Desktop/loom/experiments/bench
BACKEND=/Users/tugayakbas/Desktop/loom/backend
export PYTHONPATH=$BACKEND:$BENCH
export DATABASE_URL="postgresql+psycopg://loom:loom@localhost:5432/loom"
PY=$BACKEND/.venv/bin/python
cd $BACKEND

say() { echo "[$(date '+%H:%M:%S')] $*" | tee -a $BENCH/logs/overnight.log }

# Stage 0 — wait for the price backfill that must precede scoring, because the
# momentum and valuation factors are computed from stored bars and a score
# built before the backfill silently omits them.
if [ -n "${PRICE_PID:-}" ]; then
  say "waiting for price backfill (pid $PRICE_PID)"
  while ps -p $PRICE_PID > /dev/null 2>&1; do sleep 30; done
fi
say "price backfill finished"

# Stage 0b — deepen the fundamentals before anything is scored. The factor
# layer needs a filing within 400 days of the decision date, so a thin
# pre-2020 fact table caps the early cross-sections regardless of how much
# price history exists. Idempotent: facts dedupe on a content hash.
if [ "${SKIP_INGEST:-0}" = "1" ]; then
  say "stage 0b skipped (fundamentals already backfilled)"
else
  say "stage 0b: fundamentals backfill (16 years, idempotent)"
  $PY -m scripts.backfill_fundamentals >> $BENCH/logs/fundamentals.log 2>&1
  say "stage 0b done: $(tail -1 $BENCH/logs/fundamentals.log)"
fi

say "stage 1: integrity gates"
$PY -c "
from app.db.session import SessionLocal
import gates
for line in gates.run_all(SessionLocal()): print(line)
" 2>&1 | tee $BENCH/logs/gates.log

say "stage 2: point-in-time score cache"
$PY $BENCH/build_scores.py >> $BENCH/logs/scores.log 2>&1
say "stage 2 done: $(ls $BENCH/scores | wc -l | tr -d ' ') dates cached"

say "stage 3: mechanical benchmark"
$PY $BENCH/analyse.py > $BENCH/logs/analyse.log 2>&1
say "stage 3 done"

say "stage 4: reader crawl (stops when the daily quota is gone)"
$PY $BENCH/reader_run.py >> $BENCH/logs/reader.log 2>&1
say "stage 4 done"

say "stage 5: report"
$PY $BENCH/report.py > $BENCH/logs/report.log 2>&1
say "ALL STAGES COMPLETE"
