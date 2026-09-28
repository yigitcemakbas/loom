"""Produce reader books until the daily model quota runs out, then stop cleanly.

The free tier allows twenty generate_content calls per day per model, which the
first attempt at this discovered by exhausting it. So this is not a batch job
that either completes or fails; it is a crawl that makes as much progress as the
quota permits, writes every book the moment it exists, and resumes exactly where
it stopped the next time it runs.

The ordering matters more than it would with unlimited calls. The first sixteen
books form a complete 2x4x2 factorial at one decision date, chosen so that an
interrupted run still answers a registered question rather than leaving four
arms half filled. The placebo arm is inside that first block deliberately: it is
the one result that can invalidate every other, so it is not left for later.
"""
from __future__ import annotations

import json
import pathlib
import sys
import time
from datetime import date

from app.db.session import SessionLocal
from app.engine.llm_client import LLMUnavailableError

import build_scores
import packets
import reader
import spec

HERE = pathlib.Path(__file__).parent
BOOKS = HERE / "books"
PACKETS = HERE / "packet_cache"


def priority() -> list[spec.Cell]:
    """The grid is already ordered for interruption, so this is just its order.

    spec.grid() emits whole cells of core arms before any secondary arm, which
    is what makes a partial run usable: a cell missing its control contributes
    nothing to a paired contrast.
    """
    return spec.grid()


def _is_quota(exc: Exception) -> bool:
    """Tell a spent daily allowance apart from a provider having a bad minute.

    The engine already separates them: 429 / RESOURCE_EXHAUSTED means the twenty
    calls are gone until the window rolls, while a 503 reported as "under heavy
    load" is transient. Conflating them cost a whole run, which produced one
    book and reported all three models exhausted while none of them were.
    """
    m = str(exc).lower()
    return ("quota" in m and "exhaust" in m) or "resource_exhausted" in m or "429" in m


def packet_for(db, cell: spec.Cell) -> tuple[str, int]:
    """One packet per (tier, date, arm, universe seed), cached on disk.

    Cached because building one rebuilds the factor cross-section, which takes
    about a minute, and a dozen cells share the same packet.
    """
    PACKETS.mkdir(exist_ok=True)
    seed_u = cell.seed if cell.tier == 2 else 1
    key = f"t{cell.tier}__{cell.window.decide}__{cell.arm}__u{seed_u}"
    f = PACKETS / f"{key}.json"
    if f.exists():
        d = json.loads(f.read_text())
        return d["body"], d["n_names"]
    built, prov = packets.build(db, as_of=cell.window.decide, tier=cell.tier,
                               seed_for_universe=seed_u, arms=(cell.arm,))
    body = built[cell.arm]
    payload = {"body": body, "n_names": len(prov.tickers),
               "verdicts": prov.verdicts,
               "fingerprint": packets.fingerprint(body)}
    f.write_text(json.dumps(payload))
    return body, len(prov.tickers)


def main() -> int:
    BOOKS.mkdir(exist_ok=True)
    db = SessionLocal()
    # Ten packets share two decision dates and one company panel, so the bars
    # and facts are loaded once for the whole run rather than once per packet.
    # Without this each build re-read the panel from scratch, measured at 340
    # seconds against 0.4 once resident.
    build_scores._memoise_loaders()
    cells = priority()
    done = {p.stem for p in BOOKS.glob("*.json")}
    todo = [c for c in cells if c.name not in done]
    print(f"{len(cells)} cells registered, {len(done)} already produced, "
          f"{len(todo)} remaining", flush=True)

    produced, failures = 0, 0
    # The daily limit is per model, so one exhausted model must not end the run.
    # Verified by calling all three: each has its own twenty.
    exhausted: set[str] = set()

    for cell in todo:
        if cell.model in exhausted:
            continue
        try:
            body, n = packet_for(db, cell)
        except Exception as exc:
            print(f"SKIP {cell.name}: packet build failed {type(exc).__name__}: {exc}", flush=True)
            db.rollback()
            failures += 1
            continue

        book, gave_up = None, False
        for attempt in range(4):
            try:
                book = reader.read(packet=body, persona=cell.persona, seed=cell.seed,
                                   sessions=cell.window.sessions, n_names=n,
                                   model=cell.model)
                break
            except LLMUnavailableError as exc:
                if _is_quota(exc):
                    exhausted.add(cell.model)
                    print(f"QUOTA GONE for {cell.model} after {produced} books "
                          f"this run", flush=True)
                    gave_up = True
                    break
                wait = 90 * (attempt + 1)
                print(f"overloaded on {cell.model}, waiting {wait}s "
                      f"(attempt {attempt+1}/4)", flush=True)
                time.sleep(wait)
        if gave_up:
            if len(exhausted) >= len({c.model for c in todo}):
                print("every model has spent its daily allowance; stopping", flush=True)
                break
            continue
        if book is None:
            print(f"SKIP {cell.name}: still overloaded after four attempts", flush=True)
            failures += 1
            continue

        if book is None or "error" in book:
            failures += 1
            why = book.get("error") if book else "no response"
            print(f"FAIL {cell.name}: {why}", flush=True)
            # A provider failure still consumed quota, so pressing on usually
            # just burns the rest of it. Three in a row is treated as the wall.
            if failures >= 3:
                exhausted.add(cell.model)
                print(f"three consecutive failures on {cell.model}; "
                      f"treating it as unavailable", flush=True)
                failures = 0
                if len(exhausted) >= len({c.model for c in todo}):
                    break
            continue

        failures = 0
        book.update({"cell": cell.name, "tier": cell.tier, "arm": cell.arm,
                     "persona": cell.persona, "seed": cell.seed,
                     "decide": cell.window.decide.isoformat(),
                     "sessions": cell.window.sessions,
                     "packet_fingerprint": packets.fingerprint(body),
                     "n_offered": n})
        (BOOKS / f"{cell.name}.json").write_text(json.dumps(book, indent=1))
        produced += 1
        acts = {}
        for p in book["positions"]:
            acts[p["action"]] = acts.get(p["action"], 0) + 1
        print(f"OK  {cell.name}  {acts}  {book['latency_seconds']}s", flush=True)
        time.sleep(2)

    total = len(list(BOOKS.glob("*.json")))
    print(f"=== {produced} produced this run; {total} books on disk of "
          f"{len(cells)} registered ===", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
