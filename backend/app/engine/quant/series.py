"""Point-in-time access to one company's reported financials.

Every function in the quant layer reads its numbers through this module, for
one reason: **a factor computed from data the market did not have yet is not a
factor, it is a memory.** The failure is silent and it flatters everything
downstream, so the guard belongs at the boundary rather than in each caller.

Two things make that guard real here.

`filed_on` is the date the figure was *filed*, not the date the period ended.
A December quarter that reaches EDGAR in February is unknowable in January, and
ordering by period end would make it look otherwise.

Restatements are resolved to what was on file at the time. When a company
refiles a period, the two versions share a `period_end` and differ in
`filed_on`, so asking for a date before the correction returns the original
number the market actually traded on, and asking today returns the corrected
one. Taking the newest version unconditionally is the more obvious
implementation and it quietly rewrites history.

The layer holds no ORM objects and no session. It takes plain observations, so
the arithmetic above it stays pure and testable without a database.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Optional, Sequence

# Period labels as the XBRL ingest classifies them (ingestion/facts/
# sec_fundamentals.py). Flow metrics are reported over a span, balance-sheet
# metrics at an instant, and mixing the two is the classic way to produce a
# ratio that means nothing.
QUARTER = "quarter"
YEAR = "year"
INSTANT = "instant"

# How far from a target date a balance-sheet figure may sit and still be
# treated as "as at" that date. Fiscal year ends drift by a few days between
# years (52/53-week calendars), so an exact match is too strict; a fortnight is
# wide enough for the drift and far too narrow to admit a different quarter.
INSTANT_TOLERANCE_DAYS = 14

# How far from twelve months a pair of balance-sheet dates may sit and still be
# called a year apart. Wide enough for a 52/53-week calendar shift or a changed
# fiscal year end, narrow enough that two quarters can never satisfy it.
YEAR_TOLERANCE_DAYS = 60


@dataclass(frozen=True)
class Observation:
    """One reported figure, with everything needed to judge whether it was
    knowable at a given moment."""

    metric: str
    period: str
    period_end: date
    filed_on: date
    value: float

    @property
    def age_days(self) -> int:
        """How stale the figure was when it was filed. A large gap means the
        period closed long before anyone could act on it."""
        return (self.filed_on - self.period_end).days


class FactSeries:
    """One company's observations, queried as of a moment in time.

    Construct once per company and narrow with `as_of`. The narrowing is a
    filter rather than a new query, because a scoring run asks the same company
    for a dozen metrics and re-reading the database per metric is the mistake
    the statistics engine's baseline cache was written to undo.
    """

    def __init__(self, observations: Iterable[Observation]):
        self._observations = sorted(
            observations, key=lambda o: (o.period_end, o.filed_on)
        )

    def __len__(self) -> int:
        return len(self._observations)

    def as_of(self, when: Optional[date]) -> "FactSeries":
        """Only what had been filed by `when`. None means everything."""
        if when is None:
            return self
        return FactSeries([o for o in self._observations if o.filed_on <= when])

    def history(self, metric: str, period: str) -> list[Observation]:
        """Every distinct period for this metric, oldest first, each resolved
        to the latest version filed.

        The de-duplication is where restatements are handled: the observations
        are already filtered to what was visible, so "latest filed" means
        latest *known*, not latest ever.
        """
        latest_by_period: dict[date, Observation] = {}
        for observation in self._observations:
            if observation.metric != metric or observation.period != period:
                continue
            existing = latest_by_period.get(observation.period_end)
            if existing is None or observation.filed_on >= existing.filed_on:
                latest_by_period[observation.period_end] = observation
        return [latest_by_period[end] for end in sorted(latest_by_period)]

    def latest(self, metric: str, period: str) -> Optional[Observation]:
        series = self.history(metric, period)
        return series[-1] if series else None

    def prior(self, metric: str, period: str, back: int = 1) -> Optional[Observation]:
        """The observation `back` periods before the most recent one.

        Indexed by position in the reported series rather than by subtracting
        365 days, because filers skip periods and change fiscal calendars, and
        a date offset silently pairs a year against eighteen months when they
        do.
        """
        series = self.history(metric, period)
        index = len(series) - 1 - back
        return series[index] if index >= 0 else None

    def pair(
        self, metric: str, period: str, back: int = 1
    ) -> Optional[tuple[Observation, Observation]]:
        """The most recent observation and its comparison period, or None if
        either is missing. Returned together so a caller cannot accidentally
        compare figures from two different metrics' calendars."""
        current = self.latest(metric, period)
        previous = self.prior(metric, period, back=back)
        if current is None or previous is None:
            return None
        return current, previous

    def value(self, metric: str, period: str) -> Optional[float]:
        observation = self.latest(metric, period)
        return observation.value if observation else None

    def instant_on(
        self, metric: str, on: date, tolerance_days: int = INSTANT_TOLERANCE_DAYS
    ) -> Optional[Observation]:
        """The balance-sheet figure as at a date, or None if none is close enough.

        Balance-sheet metrics are reported at every quarter end, not only at
        the fiscal year end, so "the latest assets figure" is usually a
        mid-year one. Dividing an annual profit by it produces a ratio whose
        numerator and denominator describe different periods, which is the
        single easiest way to make this engine wrong while it still looks
        right.
        """
        candidates = self.history(metric, INSTANT)
        if not candidates:
            return None
        closest = min(candidates, key=lambda o: abs((o.period_end - on).days))
        if abs((closest.period_end - on).days) > tolerance_days:
            return None
        return closest

    def year_apart_instants(
        self, metric: str, *, anchor: Optional[date] = None
    ) -> Optional[tuple[Observation, Observation]]:
        """A balance-sheet figure and the one from roughly a year earlier.

        Taking the two most recent instants instead is the obvious
        implementation and it compares consecutive quarters, then reports the
        result as annual growth. Every company in this database files
        quarterly, so that mistake would apply to all of them at once and
        nothing in the output would look wrong.
        """
        history = self.history(metric, INSTANT)
        if not history:
            return None

        current = (
            self.instant_on(metric, anchor) if anchor is not None else history[-1]
        )
        if current is None:
            return None

        target = current.period_end - timedelta(days=365)
        prior = min(
            (o for o in history if o.period_end < current.period_end),
            key=lambda o: abs((o.period_end - target).days),
            default=None,
        )
        if prior is None:
            return None
        if abs((prior.period_end - target).days) > YEAR_TOLERANCE_DAYS:
            return None
        return current, prior

    def aligned(
        self, metrics: Sequence[str], period: str = YEAR, *, back: int = 0
    ) -> Optional[tuple[date, dict[str, float]]]:
        """Several flow metrics drawn from one and the same reporting period.

        A factor that combines metrics must take them from the same period or
        it is describing two different years at once. This is not hypothetical:
        filers change which XBRL concept they tag revenue under, so one metric
        can stop updating while its neighbours carry on, and the naive pairing
        silently compares this year's profit against a four-year-old revenue.

        `back` steps the whole aligned set to an earlier period, so a
        year-over-year comparison moves every metric together.
        """
        by_metric: dict[str, dict[date, float]] = {}
        for metric in metrics:
            series = self.history(metric, period)
            if not series:
                return None
            by_metric[metric] = {o.period_end: o.value for o in series}

        shared = set.intersection(*(set(v) for v in by_metric.values()))
        if not shared:
            return None
        ordered = sorted(shared, reverse=True)
        if back >= len(ordered):
            return None
        chosen = ordered[back]
        return chosen, {m: by_metric[m][chosen] for m in metrics}

    def is_stale(self, period_end: date, *, limit_days: int) -> bool:
        """Whether a period is too old to describe the company now.

        A factor computed from internally consistent figures can still be
        useless: a revenue growth rate from a fiscal year that ended four years
        ago is arithmetically correct and, presented without a date, a lie.
        """
        reference = self.newest_filing
        if reference is None:
            return True
        return (reference - period_end).days > limit_days

    @property
    def newest_filing(self) -> Optional[date]:
        """When this company last told the market anything. A score built on a
        company that has not filed in two years is stale regardless of how
        clean its arithmetic is."""
        if not self._observations:
            return None
        return max(o.filed_on for o in self._observations)


def observations_from_facts(facts: Iterable) -> list[Observation]:
    """Adapt stored facts into the plain form this layer reasons over.

    The one place allowed to read a StructuredFact, mirroring the boundary
    statistics/features.py keeps for signals. Facts missing the attributes a
    factor needs are dropped rather than defaulted: an invented period label
    would pair a quarter against a year.
    """
    out: list[Observation] = []
    for fact in facts:
        attributes = fact.attributes or {}
        metric = attributes.get("metric")
        period = attributes.get("period")
        period_end = attributes.get("period_end")
        if not metric or not period or not period_end or fact.value is None:
            continue
        try:
            parsed_end = date.fromisoformat(period_end)
        except (TypeError, ValueError):
            continue
        out.append(
            Observation(
                metric=metric,
                period=period,
                period_end=parsed_end,
                filed_on=fact.as_of_date,
                value=float(fact.value),
            )
        )
    return out


__all__ = [
    "INSTANT",
    "INSTANT_TOLERANCE_DAYS",
    "YEAR_TOLERANCE_DAYS",
    "QUARTER",
    "YEAR",
    "FactSeries",
    "Observation",
    "observations_from_facts",
]
