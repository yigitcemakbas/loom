"""Historical baseline computation for evidence scoring.

This module acts as the bridge between the database (SignalRepository)
and our pure mathematical functions. It extracts a specific numerical
metric from a company's past signals so we know what "normal" looks like.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

# Importing the database models and the repository that fetches them
from app.models.signal import Signal, SignalType
from app.repositories.signal_repository import SignalRepository

# Importing the pure math functions we just wrote
from app.engine.statistics.statistics import (
    calculate_mean,
    calculate_variance,
    calculate_standard_deviation
)


@dataclass
class SignalBaseline:
    """The mathematical baseline for a specific type of signal.

    Instead of returning a loose tuple of numbers, we pack the results
    into this strictly typed data structure so the engine knows exactly
    what it is looking at.
    """
    signal_type: SignalType
    metric_name: str
    mean: float
    standard_deviation: float
    sample_size: int


def get_historical_baseline(
        company_id: str,
        signal_type: SignalType,
        metric_name: str,
        repository: SignalRepository,
        days_back: int = 90
) -> SignalBaseline | None:
    """Fetches historical signals and computes the baseline for a metric.

    Args:
        company_id: The UUID of the company.
        signal_type: The kind of signal (e.g., SENTIMENT_SHIFT).
        metric_name: The attribute to measure (e.g., 'sentiment_score').
        repository: The database access object.
        days_back: How far back to look to establish "normal".

    Returns:
        A SignalBaseline object, or None if there isn't enough history to
        form a mathematically valid baseline.
    """
    # 1. Define the time window (anchored to UTC to avoid timezone bugs)
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_back)

    # 2. Fetch the raw signals from the database
    # We use a high limit to ensure we capture the full historical distribution.
    historical_signals = repository.list_feed(
        company_id=company_id,
        signal_type=signal_type,
        since=cutoff_date,
        limit=500
    )

    # 3. Extract the specific numerical values we want to measure
    values = []
    for signal in historical_signals:
        # getattr dynamically fetches a property by its string name.
        # If metric_name is "confidence", this gets signal.confidence.
        val = getattr(signal, metric_name, None)

        # We strictly verify it is a number before trusting it.
        if isinstance(val, (int, float)):
            values.append(float(val))

    # 4. Guardrail: We need at least 2 data points.
    # Variance is undefined for a single data point.
    if len(values) < 2:
        return None

    # 5. Route the raw numbers to our pure math functions
    mean_val = calculate_mean(values)
    variance_val = calculate_variance(values, mean_val)
    std_dev = calculate_standard_deviation(variance_val)

    return SignalBaseline(
        signal_type=signal_type,
        metric_name=metric_name,
        mean=mean_val,
        standard_deviation=std_dev,
        sample_size=len(values)
    )