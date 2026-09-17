"""Statistical primitives for evidence evaluation.

These are pure functions by design. They do not know about the database,
the ORM, or the LLM. Keeping the math isolated ensures the reasoning engine
remains deterministic, fast, and exhaustively testable without mocking
external dependencies.
"""

import math
from typing import Optional, Sequence


def calculate_mean(values: Sequence[float]) -> float:
    """The arithmetic average of a historical baseline.

    Refuses to guess when handed an empty sequence. Returning 0.0 for a
    company with no history would silently treat a lack of evidence as a
    baseline of zero, distorting subsequent calculations.
    """
    if not values:
        raise ValueError("Cannot calculate mean: no historical values provided.")
    return sum(values) / len(values)


def calculate_variance(values: Sequence[float], mean_value: float) -> float:
    """The spread of historical observations around their mean.

    Calculates population variance. Since this evaluates the entirety of a
    company's known baseline rather than a sampled subset, Bessel's
    correction (N-1) is intentionally omitted.
    """
    if not values:
        raise ValueError("Cannot calculate variance on empty data.")

    squared_deviations = [(x - mean_value) ** 2 for x in values]
    return sum(squared_deviations) / len(values)


def calculate_standard_deviation(variance: float) -> float:
    """Restores variance to the original unit scale."""
    if variance < 0:
        raise ValueError("Variance cannot be negative.")
    return math.sqrt(variance)


def calculate_z_score(value: float, mean_value: float, std_dev: float) -> Optional[float]:
    """How far a value sits from a mean, in standard deviations.

    Returns None when the standard deviation is zero, because the answer is
    genuinely undefined rather than zero. The earlier version returned 0.0,
    which a caller reads as "perfectly average" and therefore "not anomalous":
    a company whose history is perfectly flat would have its first departure
    from that history reported as unremarkable, which is the exact case the
    engine exists to catch. None forces the caller to distinguish "not
    anomalous" from "not computable".

    Retained for continuous metrics. It is deliberately *not* used for
    magnitude, which is categorical; see `shrunk_rate`.
    """
    if std_dev == 0.0:
        return None

    return (value - mean_value) / std_dev


def shrunk_rate(
    successes: int,
    total: int,
    prior_rate: float,
    prior_weight: float,
) -> float:
    """A proportion pulled toward a prior in proportion to how little data backs it.

    This is the standard empirical-Bayes estimate for a rate: the posterior
    mean of a Beta prior with `prior_weight` pseudo-observations distributed at
    `prior_rate`. It exists because raw proportions are wildly unstable at the
    sample sizes actually available here. One "major" finding out of two is not
    evidence that half of a company's findings are major, but that is precisely
    what an unshrunk rate claims.

    The behaviour is self-correcting at both ends. With no local history the
    estimate is exactly the prior; as the company accumulates findings its own
    rate takes over smoothly, with no threshold at which the answer jumps.
    """
    if total < 0 or successes < 0:
        raise ValueError("Counts cannot be negative.")
    if successes > total:
        raise ValueError("Successes cannot exceed the number of observations.")
    if not 0.0 <= prior_rate <= 1.0:
        raise ValueError("Prior rate must be a probability.")
    if prior_weight < 0:
        raise ValueError("Prior weight cannot be negative.")

    denominator = total + prior_weight
    if denominator == 0:
        return prior_rate
    return (successes + prior_weight * prior_rate) / denominator


def rate_lift(observed_rate: float, reference_rate: float) -> Optional[float]:
    """How many times more often something happens here than in general.

    Returns None when the reference rate is zero, since the comparison has no
    meaning: everything is infinitely more common than something that never
    occurs.
    """
    if reference_rate <= 0.0:
        return None
    return observed_rate / reference_rate
