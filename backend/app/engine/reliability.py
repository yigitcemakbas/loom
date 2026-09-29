"""Closing the loop: what each kind of finding has been worth, fed back into ranking.

`engine/evaluation.py` has been able to measure whether findings predicted
anything for most of this project's life, and gated nothing. `engine/priority.py`
ranks them by a hand-written table of type weights that nothing has ever checked.
This module is the wire between the two.

**Two guards make it safe to connect them, and both matter more than the
arithmetic.**

*It stays inert until it has earned the right not to be.* A type's multiplier is
exactly 1.0 — the hand-written weight, untouched — unless it clears a sample floor
and a t-statistic bar. Eight signal types against 695 findings puts every one of
them below the floor today, so switching this on changes nothing, which is the
point: fitting eight weights to a few dozen noisy observations each is precisely
the mistake `quant/relevance.py` refuses by name.

*It is bounded even once trusted.* A measured edge moves a weight within
[MULTIPLIER_FLOOR, MULTIPLIER_CEILING] and no further. A feedback loop with
unbounded gain will find any bias in its own measurement and amplify it, and the
measurement here shares a corpus with the thing being measured.

**And it measures the direction the engine actually uses.** `evaluate_signals`
scored `market_direction` — the model's expected market reaction, which no longer
drives the stance. Feeding that back would tune the ranking on a field the product
had stopped reading. Reliability is measured on the documentary direction from
`engine/direction.py` instead, and the older field is still measurable separately,
which makes "does the reaction guess beat the documentary read" an answerable
question rather than an assumption.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.reliability import SignalReliability
from app.models.signal import SignalType

logger = logging.getLogger(__name__)

# Directional calls a type needs before its record is allowed to move anything.
# Matches evaluation.MIN_SAMPLE_FOR_VERDICT rather than inventing a second bar:
# the threshold for "these numbers mean much" should not depend on who is asking.
MIN_OBSERVATIONS = 30

# How far from zero the spread's t-statistic must sit. Two is the conventional
# bar and is deliberately not corrected for multiplicity here, because the
# multiplier is bounded and a false positive costs a mild reweighting rather than
# a claim. `evaluation.survives_multiple_testing` is the right tool when the
# question is whether to *believe* a result.
MIN_T_STATISTIC = 2.0

# The band a measured record may move a weight within.
#
# Bounded because this is a feedback loop whose measurement shares a corpus with
# the thing it measures, and an unbounded loop will find any bias in that
# measurement and amplify it.
#
# Narrowed from [0.7, 1.3] once a test showed that band was wide enough to invert
# the evidence-quality ordering the type table exists to encode: a best-case quote
# scored 0.6 x 1.3 = 0.78 against a worst-case risk diff at 1.0 x 0.7 = 0.70. That
# ordering is a stated epistemology — a deterministic two-filing comparison
# outranks an unverifiable language judgement — and a measured edge on a few dozen
# observations should refine it, not overturn it. At these bounds the extremes
# cannot cross: 0.6 x 1.25 = 0.75 stays below 1.0 x 0.8 = 0.80.
#
# Adjacent types can still swap, which is intended. The table's fine gradations
# are judgement; its top-to-bottom ordering is the claim.
MULTIPLIER_FLOOR = 0.8
MULTIPLIER_CEILING = 1.25

TTL_SECONDS = 900


@dataclass(frozen=True)
class Reliability:
    """One signal type's track record, and what ranking is allowed to do with it."""

    signal_type: str
    observations: int
    hit_rate: Optional[float]
    baseline_hit_rate: Optional[float]
    spread: Optional[float]
    t_statistic: Optional[float]

    @property
    def edge(self) -> Optional[float]:
        if self.hit_rate is None or self.baseline_hit_rate is None:
            return None
        return self.hit_rate - self.baseline_hit_rate

    @property
    def trusted(self) -> bool:
        """Whether this record may affect ranking.

        Three conditions, all required. Enough observations, a spread far enough
        from zero to be distinguishable from chance, and an edge over the no-skill
        baseline — because a type can clear a t-test on a spread that is still
        worse than always guessing whichever direction dominated the period.
        """
        if self.observations < MIN_OBSERVATIONS:
            return False
        if self.t_statistic is None or abs(self.t_statistic) < MIN_T_STATISTIC:
            return False
        return self.edge is not None

    @property
    def multiplier(self) -> float:
        """The bounded adjustment to this type's hand-written weight.

        Exactly 1.0 when untrusted, so an unmeasured or thinly-measured type keeps
        the weight a human wrote for a stated reason.
        """
        if not self.trusted:
            return 1.0
        edge = self.edge or 0.0
        # An edge of ten points over baseline reaches the ceiling; the same
        # against it reaches the floor. Linear in between, then clamped.
        scaled = 1.0 + (edge / 0.10) * (MULTIPLIER_CEILING - 1.0)
        return round(max(MULTIPLIER_FLOOR, min(MULTIPLIER_CEILING, scaled)), 4)


def measure(db: Session, *, horizon: int = 5, limit: int = 4000) -> dict[str, Reliability]:
    """Measure every signal type's record at one horizon.

    Uses the documentary direction, clusters by event before summarising, and
    inherits every guard `evaluation.py` already applies: benchmark-adjusted
    returns, entry strictly after the event, and one directional call per company
    per day rather than one per finding.
    """
    from app.engine import evaluation
    from app.engine.direction import documentary_sign
    from app.repositories.company_repository import CompanyRepository
    from app.repositories.signal_repository import SignalRepository

    tickers = {c.id: c.ticker for c in CompanyRepository(db).list_all()}
    signals = SignalRepository(db).list_feed(limit=limit)

    by_type: dict[str, list] = {}
    for signal in signals:
        if signal.company_id not in tickers:
            continue
        sign = documentary_sign(signal)
        direction = (
            "neutral" if sign is None else "positive" if sign > 0 else "negative"
        )
        key = str(getattr(signal.signal_type, "value", signal.signal_type))
        by_type.setdefault(key, []).append(
            evaluation.Outcome(
                subject_id=str(signal.id),
                ticker=tickers[signal.company_id],
                occurred_at=signal.occurred_at,
                predicted_direction=direction,
                strength=signal.priority or 0.0,
            )
        )

    cache = evaluation._PriceCache(db)
    out: dict[str, Reliability] = {}
    for key, raw in by_type.items():
        scored, skipped = evaluation._score_outcomes(raw, horizon, cache)
        report = evaluation.summarise(
            key, horizon, evaluation.cluster_by_event(scored), skipped
        )
        out[key] = Reliability(
            signal_type=key,
            observations=report.directional,
            hit_rate=report.hit_rate,
            baseline_hit_rate=report.baseline_hit_rate,
            spread=report.spread,
            t_statistic=report.t_statistic,
        )
    return out


def persist(db: Session, measured: dict[str, Reliability], *, horizon: int) -> int:
    """Store the measured records, replacing this horizon's rows."""
    db.query(SignalReliability).filter(
        SignalReliability.horizon_sessions == horizon
    ).delete(synchronize_session=False)
    for key, record in measured.items():
        db.add(SignalReliability(
            signal_type=key,
            horizon_sessions=horizon,
            observations=record.observations,
            hit_rate=record.hit_rate,
            baseline_hit_rate=record.baseline_hit_rate,
            spread=record.spread,
            t_statistic=record.t_statistic,
            trusted=record.trusted,
            multiplier=record.multiplier,
        ))
    db.commit()
    trusted = sum(1 for r in measured.values() if r.trusted)
    logger.info(
        "Reliability: %d types measured at %d sessions, %d trusted enough to "
        "affect ranking.", len(measured), horizon, trusted,
    )
    return trusted


_lock = threading.Lock()
_cached: Optional[tuple[datetime, dict[str, float]]] = None


def multipliers(db: Session, *, horizon: int = 5, force: bool = False) -> dict[str, float]:
    """The stored multipliers by signal type, reused within a time-to-live.

    Never raises into a caller. Without a table every weight is 1.0, which is the
    ranking the engine had before this module existed — the honest degradation.
    """
    global _cached
    now = datetime.now(timezone.utc)
    with _lock:
        if not force and _cached and now - _cached[0] < timedelta(seconds=TTL_SECONDS):
            return _cached[1]
    try:
        rows = db.execute(
            select(SignalReliability.signal_type, SignalReliability.multiplier)
            .where(SignalReliability.horizon_sessions == horizon)
            .where(SignalReliability.trusted.is_(True))
        ).all()
        found = {t: float(m) for t, m in rows}
    except Exception:
        logger.exception("Reliability: could not load multipliers; ranking unweighted.")
        return {}
    with _lock:
        _cached = (now, found)
    return found


def multiplier_for(signal_type, measured: dict[str, float]) -> float:
    """Look up one type's multiplier, defaulting to no adjustment."""
    key = str(getattr(signal_type, "value", signal_type))
    return measured.get(key, 1.0)


__all__ = [
    "MIN_OBSERVATIONS",
    "MIN_T_STATISTIC",
    "MULTIPLIER_CEILING",
    "MULTIPLIER_FLOOR",
    "Reliability",
    "measure",
    "multiplier_for",
    "multipliers",
    "persist",
]
