"""The checks that must pass before a single book is produced.

A benchmark whose integrity checks have never been seen to fail is a benchmark
resting on an assumption. Each gate here is paired with a deliberate violation,
so the run proves its audits have teeth rather than asserting it.
"""
from __future__ import annotations

import math
from collections import Counter
from datetime import date, timedelta

import packets
import spec


def leak_canary(db, as_of: date) -> str:
    """Inject a future date into a packet and require the audit to catch it."""
    future = (as_of + timedelta(days=30)).isoformat()
    poisoned = {"control": f"## AAPL\nfiled {future}\n"}
    try:
        packets.text_audit(poisoned, as_of)
    except packets.LookaheadError:
        return "PASS text audit rejected a future-dated packet"
    return "FAIL text audit accepted a future-dated packet"


def provenance_canary(db, as_of: date) -> str:
    """A provenance record claiming a future finding must be refused."""
    prov = packets.Provenance(as_of=as_of, tickers=["AAPL"],
                              newest_signal=as_of + timedelta(days=1))
    try:
        prov.audit(db)
    except packets.LookaheadError:
        return "PASS provenance audit rejected a future finding"
    return "FAIL provenance audit accepted a future finding"


def verdict_entropy(prov: packets.Provenance) -> tuple[float, Counter]:
    """How much the verdict actually varies across the universe.

    Near zero means every company got the same answer, and no outcome
    difference in the `full` arm can be attributed to Loom discriminating
    between companies. It gates the interpretation of the primary endpoint.
    """
    counts = Counter(prov.verdicts.values())
    n = sum(counts.values())
    if n == 0:
        return 0.0, counts
    h = -sum((c / n) * math.log2(c / n) for c in counts.values() if c)
    return h, counts


def manipulation_strength(built: dict[str, str]) -> dict[str, float]:
    """How different each arm's packet really is from the one it is compared to.

    A null result on an arm whose packet barely differs is a null about the
    manipulation, not about Loom, and the two must not be reported alike.
    """
    def words(body: str) -> Counter:
        return Counter(w for w in body.lower().split() if len(w) > 3)

    out: dict[str, float] = {}
    for arm, body in built.items():
        ref = "control" if arm in ("evidence", "control", "quant_loom", "verdict_only") else "evidence"
        other = built.get(ref, "")
        if not other or arm == ref:
            continue
        # Compared block by block, not packet against packet. The placebo arm
        # is why: it hands each company another company's evidence, so the
        # packet as a whole contains the same words in the same quantities and
        # both a length ratio and a whole-packet Jaccard scored it 0.009. What
        # changed is which company each block belongs to, and only a per-company
        # comparison can see it.
        mine = body.split("\n\n---\n\n")
        theirs = other.split("\n\n---\n\n")
        scores = []
        for x, y in zip(mine, theirs):
            a, b = words(x), words(y)
            inter = sum((a & b).values())
            union = sum((a | b).values()) or 1
            scores.append(1 - inter / union)
        out[arm] = round(sum(scores) / len(scores), 4) if scores else 0.0
    return out


def run_all(db) -> list[str]:
    lines = []
    as_of = spec.PRIMARY_WINDOWS[-1].decide
    lines.append(leak_canary(db, as_of))
    lines.append(provenance_canary(db, as_of))
    built, prov = packets.build(db, as_of=as_of, tier=1)
    h, counts = verdict_entropy(prov)
    lines.append(f"INFO verdict entropy {h:.3f} bits over {len(prov.verdicts)} names: {dict(counts)}")
    if h < 0.5:
        lines.append("WARN verdict is near-constant; `full` measures a uniform signal, "
                     "not Loom discriminating between companies")
    strength = manipulation_strength(built)
    lines.append("INFO packet divergence vs its comparator: " +
                 ", ".join(f"{k}={v:.3f}" for k, v in sorted(strength.items())))
    lines.append(f"INFO provenance newest signal={prov.newest_signal} bar={prov.newest_bar} "
                 f"fact={prov.newest_fact_filed} decision={as_of}")
    return lines
