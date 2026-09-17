"""Statistical evidence scoring engine.

Scores one finding against what that company's findings normally look like,
so a severity that is routine for one issuer can register as unusual for
another. Deterministic and auditable: every score carries the counts it came
from.

**What replaced the z-score, and why.** Magnitude is a three-valued label, and
a z-score on it is not meaningful: the "beyond two sigma is roughly the 95th
percentile" reading assumes an approximately normal distribution, which a
variable with three possible values does not have at any sample size. What the
question actually reduces to is a rate. If a tenth of a company's findings are
normally "major" and this one is, that is ordinary; if a fiftieth are, it is
not. So the engine estimates the rate of the observed label for that company
and reports how unusual the label is, with no distributional assumption.

Small samples are handled by shrinking each rate toward the cross-company rate
rather than by trusting a proportion drawn from three observations.
"""

from dataclasses import dataclass
from collections import Counter
from datetime import datetime, timedelta
from typing import Callable, Optional

from app.engine.statistics.baseline import (
    DEFAULT_DAYS_BACK,
    CategoryBaseline,
    build_baseline,
)
from app.engine.statistics.features import (
    MAGNITUDE_LABELS,
    EvidenceFeature,
    magnitude_label_of,
)
from app.engine.statistics.statistics import rate_lift
from app.models.signal import SignalType
from app.repositories.signal_repository import SignalRepository

# A label occurring in less than this share of a company's findings is treated
# as a departure from its norm. Expressed as a rate rather than a sigma count
# so the threshold means something concrete: "fewer than one finding in ten".
ANOMALY_RATE = 0.10


@dataclass
class EvidenceScore:
    """The assessment of a single feature against its company's history.

    Carries the baseline that judged it so the interface can show why a finding
    matters rather than asserting that it does.
    """

    feature: EvidenceFeature
    baseline: Optional[CategoryBaseline]
    # Shrunk probability of this finding's magnitude label for this company.
    # None when the label was never assessed or no baseline could be formed.
    rate: Optional[float] = None
    # How much more often this company produces this label than the population.
    lift: Optional[float] = None

    @property
    def is_anomalous(self) -> bool:
        """Whether this finding's severity is unusual for this company.

        False whenever `rate` is None. An unproven anomaly is not an anomaly:
        with no assessed magnitude or no baseline there is nothing to be
        unusual against, and defaulting to True would let missing data inflate
        a company's risk profile.
        """
        return self.rate is not None and self.rate < ANOMALY_RATE

    @property
    def is_computable(self) -> bool:
        """Whether a verdict was actually reached, as distinct from a negative
        one. Lets a caller tell "normal" apart from "not enough history"."""
        return self.rate is not None


class BaselineCache:
    """Holds each company's history for the span of one scoring run.

    Every finding of a given type for a given company is judged against the
    same history, so querying per finding is pure repetition. The first version
    of this cache keyed on the excluded signal id, which is unique per finding
    and therefore never hit: it issued *more* queries than no cache at all.

    So the history is fetched once per (company, signal type) over the widest
    window any finding could need, and the per-finding work (restricting to that
    finding's own window, and removing the finding itself) is arithmetic over
    the rows already in memory.
    """

    def __init__(self, repository: SignalRepository, days_back: int = DEFAULT_DAYS_BACK):
        self.repository = repository
        self.days_back = days_back
        self._history: dict[tuple, list[tuple]] = {}
        self._priors: dict[SignalType, list[tuple]] = {}

    def population_rates(
        self, signal_type: SignalType, as_of: datetime, extractor
    ) -> dict[str, float]:
        """Cross-company label rates as of a moment, windowed in memory.

        Fetched once per signal type for the same reason company history is:
        keying the query by day made the prior cost a round trip per distinct
        occurrence date, which on a real run was most of the queries left.
        """
        history = self._population_history(signal_type, as_of, extractor)
        window_start = as_of - timedelta(days=self.days_back)

        counts: Counter = Counter()
        for occurred_at, label in history:
            if label is not None and window_start <= occurred_at < as_of:
                counts[label] += 1

        total = sum(counts.values())
        if total == 0:
            # Nothing to learn from; a flat prior is a weaker claim than
            # asserting rates of zero.
            return {label: 1.0 / len(MAGNITUDE_LABELS) for label in MAGNITUDE_LABELS}
        return {label: counts.get(label, 0) / total for label in MAGNITUDE_LABELS}

    def _population_history(self, signal_type: SignalType, newest: datetime, extractor):
        if signal_type not in self._priors:
            signals = self.repository.list_for_global_prior(
                signal_type=signal_type,
                since=newest - timedelta(days=self.days_back * 2),
            )
            self._priors[signal_type] = [
                (sig.occurred_at, extractor(sig)) for sig in signals
            ]
        return self._priors[signal_type]

    def _history_for(self, company_id, signal_type: SignalType, newest: datetime, extractor):
        key = (str(company_id), signal_type)
        if key not in self._history:
            signals = self.repository.list_for_baseline(
                company_id=company_id,
                signal_type=signal_type,
                # Widest span any finding in this run could ask for.
                since=newest - timedelta(days=self.days_back * 2),
            )
            self._history[key] = [
                (str(sig.id), sig.occurred_at, extractor(sig)) for sig in signals
            ]
        return self._history[key]

    def baseline(
        self,
        company_id,
        signal_type: SignalType,
        as_of: datetime,
        extractor,
        exclude_signal_id=None,
    ) -> Optional[CategoryBaseline]:
        history = self._history_for(company_id, signal_type, as_of, extractor)

        window_start = as_of - timedelta(days=self.days_back)
        excluded = str(exclude_signal_id) if exclude_signal_id is not None else None

        counts: Counter = Counter()
        for signal_id, occurred_at, label in history:
            if label is None or signal_id == excluded:
                continue
            # Strictly before `as_of`: a baseline may not contain the future,
            # and must not contain the observation it is about to judge.
            if window_start <= occurred_at < as_of:
                counts[label] += 1

        return build_baseline(
            counts, signal_type, self.population_rates(signal_type, as_of, extractor)
        )


def evaluate_evidence(
    feature: EvidenceFeature,
    company_id,
    repository: SignalRepository,
    *,
    signal_type: Optional[SignalType] = None,
    exclude_signal_id=None,
    cache: Optional[BaselineCache] = None,
    extractor: Callable[[object], Optional[str]] = magnitude_label_of,
) -> EvidenceScore:
    """Score one finding against its company's own history."""
    if feature.magnitude_label is None:
        # Never assessed for market impact, so there is nothing to compare.
        return EvidenceScore(feature=feature, baseline=None)

    resolved_type = signal_type or SignalType(feature.signal_type)
    cache = cache or BaselineCache(repository)

    baseline = cache.baseline(
        company_id,
        resolved_type,
        feature.occurred_at,
        extractor,
        exclude_signal_id=exclude_signal_id,
    )
    if baseline is None:
        return EvidenceScore(feature=feature, baseline=None)

    rate = baseline.rate_for(feature.magnitude_label)
    lift = rate_lift(rate, baseline.prior_rates.get(feature.magnitude_label, 0.0)) if rate else None

    return EvidenceScore(feature=feature, baseline=baseline, rate=rate, lift=lift)


__all__ = [
    "ANOMALY_RATE",
    "BaselineCache",
    "EvidenceScore",
    "MAGNITUDE_LABELS",
    "evaluate_evidence",
]
