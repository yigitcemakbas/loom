"""Write the three arm packets for each period. Fails rather than leaks."""
import pathlib
from datetime import date
from app.db.session import SessionLocal
import harness

OUT = pathlib.Path(__file__).parent / "packets"
PERIODS = [harness.Period(date(2026, 3, 25), date(2026, 6, 25)),
           harness.Period(date(2026, 6, 25), date(2026, 9, 25))]
SIZE = 25

OUT.mkdir(exist_ok=True)
db = SessionLocal()
for p in PERIODS:
    u = harness.pick_universe(db, p.start, SIZE)
    packs = harness.build_packets(db, p, u)
    (OUT / f"{p.label}_universe.txt").write_text("\n".join(u))
    for arm, text in packs.items():
        f = OUT / f"{p.label}_{arm}.md"
        f.write_text(text)
        print(f"  {f.name:44s} {len(text):7d} chars")
    print(f"{p.label}: {len(u)} companies, audit passed\n")
