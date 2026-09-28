"""Check every book is well formed before it reaches the scorer.

A book with a missing ticker, a duplicate, or gross exposure over the limit
produces a number rather than an error, and the number looks fine.
"""
import json, pathlib, sys

BOOKS = pathlib.Path(__file__).parent / "books"
PACKS = pathlib.Path(__file__).parent / "packets"
ok = True
for f in sorted(BOOKS.glob("*.json")):
    plabel = f.stem.split("__")[0]
    want = set((PACKS / f"{plabel}_universe.txt").read_text().split())
    try:
        raw = json.loads(f.read_text())
    except Exception as e:
        print(f"  BROKEN  {f.name}: {e}"); ok = False; continue
    got = [x["ticker"] for x in raw]
    gross = sum(abs(float(x["weight"])) for x in raw if x["action"] in ("buy","short","sell"))
    problems = []
    if len(got) != len(set(got)): problems.append("duplicate tickers")
    if set(got) != want:
        if want - set(got): problems.append(f"missing {sorted(want-set(got))}")
        if set(got) - want: problems.append(f"extra {sorted(set(got)-want)}")
    if gross > 1.001: problems.append(f"gross exposure {gross:.2f} over 1.0")
    # A stub is a failure; terseness is not. The 40-character bar was
    # inherited from the API and rejected "Up 4842% is insane, this is pure
    # bubble" at 39, which is a complete reason from a persona written to be
    # plain-spoken. Empty or placeholder text still fails.
    stubs = [x["ticker"] for x in raw if len(x.get("rationale","").strip()) < 20]
    if stubs: problems.append(f"stub rationale: {stubs}")
    terse = sum(1 for x in raw if 20 <= len(x.get("rationale","").strip()) < 40)
    longs = sum(1 for x in raw if x["action"]=="buy")
    shorts = sum(1 for x in raw if x["action"] in ("short","sell"))
    flag = "OK  " if not problems else "FAIL"
    note = f"  ({terse} terse)" if terse else ""
    if problems: ok = False
    print(f"  {flag} {f.name:62s} n={len(got):2d} gross={gross:.2f} "
          f"L{longs} S{shorts} P{len(got)-longs-shorts}"
          + note + ("  <- " + "; ".join(problems) if problems else ""))
sys.exit(0 if ok else 1)
