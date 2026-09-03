"""Statistical primitives for evidence evaluation.

These are pure functions by design. They do not know about the database,
the ORM, or the LLM. Keeping the math isolated ensures the reasoning engine
remains deterministic, fast, and exhaustively testable without mocking
external dependencies.
"""

import math
from typing import Sequence


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


def calculate_z_score(value: float, mean_value: float, std_dev: float) -> float:
    """Measures how anomalous a finding is against the company's own history.

    A raw 5% move means nothing without context. The z-score normalizes the
    magnitude against the stock's historical volatility.

    When standard deviation is zero (the company's history is perfectly flat),
    any deviation is mathematically undefined. We clamp it to 0.0 rather than
    crashing, as a perfectly flat history implies the signal cannot be
    meaningfully compared.
    """
    if std_dev == 0.0:
        return 0.0

    return (value - mean_value) / std_dev