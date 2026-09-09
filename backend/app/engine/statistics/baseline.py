"""Historical baseline computation for evidence scoring.

Bridges the repository and the pure math functions: it reads a company's past
signals and describes what "normal" has looked like for them.

The baseline is a **table of category rates**, not a mean and a standard
deviation. Magnitude takes exactly three values, so a mean of 1.4 describes no
finding that can actually occur, and a standard deviation of it invites a
z-score whose "two sigma is the 95th percentile" reading depends on a normal
distribution that a three-valued variable cannot have. Counting how often each
label occurs makes no distributional assumption at all.
"""

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable, Optional, Sequence

from app.engine.statistics.features import MAGNITUDE_LABELS
from app.engine.statistics.statistics import shrunk_rate
from app.models.signal import SignalType
from app.repositories.signal_repository import SignalRepository

# How far back "normal" is drawn from. A year rather than a quarter: magnitude
# is assigned from disclosures that arrive on a roughly quarterly cadence, so 90
# days is about one filing cycle and yields too few assessed findings to
# estimate a rate from. Measured over the stored corpus, a 90-day window leaves
# 5 company/signal-type pairs with enough history and a 365-day window leaves
# 10, for the same statistical bar.
DEFAULT_DAYS_BACK = 365

# Below this many observations a company's own rates are not reported as its
# baseline at all. Shrinkage already keeps a thin sample from producing a
# confident answer, but a hard floor stops the engine claiming to describe a
# company it has essentially never seen.
MIN_SAMPLES = 10

# Pseudo-observations behind the cross-company prior. At this weight a company
# needs roughly its own MIN_SAMPLES of history before its rates outweigh the
# population's, which is the intended crossover.
PRIOR_WEIGHT = 10.0


@dataclass
class CategoryBaseline:
    """How often each label has occurred, for one company and signal type."""

    signal_type: SignalType
    counts: dict[str, int]
    sample_size: int
    # Rates after shrinkage toward the population. These are the numbers to
    # reason with; `counts` is kept so a conclusion can be audited.
    rates: dict[str, float] = field(default_factory=dict)
    # The population rates this baseline was shrunk toward.
    prior_rates: dict[str, float] = field(default_factory=dict)

    def rate_for(self, label: str) -> Optional[float]:
        return self.rates.get(label)


def count_labels(signals: Sequence, extractor: Callable[[object], Optional[str]]) -> Counter:
    """Tally labels, skipping observations the extractor declines to score.

    The extractor returns None for a signal that was never assessed, and such a
    signal is *absent* from the tally rather than counted as some default. Just
    under half the stored corpus is unassessed, so defaulting them would bury
    the real distribution under one synthetic value.
    """
    counts: Counter = Counter()
    for signal in signals:
        label = extractor(signal)
        if label is not None:
            counts[label] += 1
    return counts


def _rates(counts: Counter, total: int, labels: Sequence[str]) -> dict[str, float]:
    if total <= 0:
        return {label: 0.0 for label in labels}
    return {label: counts.get(label, 0) / total for label in labels}


def get_population_rates(
    signal_type: SignalType,
    repository: SignalRepository,
    *,
    as_of: datetime,
    extractor: Callable[[object], Optional[str]],
    labels: Sequence[str] = MAGNITUDE_LABELS,
    days_back: int = DEFAULT_DAYS_BACK,
) -> dict[str, float]:
    """Label rates across every company, used as the prior.

    A uniform prior would be a worse guess than the data plainly supports: if
    only 8% of all findings are ever "major", assuming a third of them are
    makes every company look unusually calm.
    """
    signals = repository.list_for_global_prior(
        signal_type=signal_type,
        since=as_of - timedelta(days=days_back),
        until=as_of,
    )
    counts = count_labels(signals, extractor)
    total = sum(counts.values())
    if total == 0:
        # Nothing to learn from; fall back to a flat prior rather than
        # dividing by zero or asserting rates of zero.
        return {label: 1.0 / len(labels) for label in labels}
    return _rates(counts, total, labels)


def build_baseline(
    counts: Counter,
    signal_type: SignalType,
    prior_rates: dict[str, float],
    *,
    labels: Sequence[str] = MAGNITUDE_LABELS,
    min_samples: int = MIN_SAMPLES,
) -> Optional[CategoryBaseline]:
    """Turn a label tally into a shrunk rate table. Pure: no database.

    Returns None when the company has fewer than `min_samples` assessed
    findings of this type, which is the honest answer rather than a confident
    one drawn from three data points. Shrinkage already stops a thin sample
    producing a strong claim; the floor stops the engine describing a company
    it has essentially never seen.
    """
    total = sum(counts.values())
    if total < min_samples:
        return None

    rates = {
        label: shrunk_rate(
            successes=counts.get(label, 0),
            total=total,
            prior_rate=prior_rates.get(label, 0.0),
            prior_weight=PRIOR_WEIGHT,
        )
        for label in labels
    }

    return CategoryBaseline(
        signal_type=signal_type,
        counts=dict(counts),
        sample_size=total,
        rates=rates,
        prior_rates=dict(prior_rates),
    )
