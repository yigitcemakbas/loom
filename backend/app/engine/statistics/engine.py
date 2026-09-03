"""Statistical evidence scoring engine.

This module orchestrates the evaluation of a single piece of evidence.
Instead of assigning a static, human-guessed weight (e.g., 0.85 for all
insider trades), it fetches the historical baseline for this specific company
and calculates exactly how abnormal the new finding is.

This keeps the reasoning fully deterministic and auditable, but makes it
context-aware. A move that is trivial for one company might be a severe
anomaly for another.
"""

from dataclasses import dataclass
from typing import Optional

from app.engine.statistics.features import EvidenceFeature
from app.engine.statistics.baseline import SignalBaseline, get_historical_baseline
from app.engine.statistics.statistics import calculate_z_score
from app.repositories.signal_repository import SignalRepository


@dataclass
class EvidenceScore:
    """The final mathematical assessment of a single feature.

    This replaces the old scalar priority weight. It carries the original
    feature, the historical context that judged it, and the resulting score,
    so the dashboard can explicitly show the user *why* a finding matters.
    """
    feature: EvidenceFeature
    baseline: Optional[SignalBaseline]
    z_score: float

    @property
    def is_anomalous(self) -> bool:
        """A simple gate for downstream synthesis.

        Statistically, a Z-score beyond 2.0 (roughly the 95th percentile)
        indicates a significant deviation from the norm.
        """
        return abs(self.z_score) > 2.0


def evaluate_evidence(
        feature: EvidenceFeature,
        company_id: str,
        repository: SignalRepository
) -> EvidenceScore:
    """Scores a new finding against the company's own historical baseline.

    If a company lacks enough history to form a baseline, we default to a
    Z-score of 0.0. An unproven anomaly must not be allowed to artificially
    inflate a company's risk profile.
    """
    # 1. Fetch the historical baseline for this specific type of signal
    # We use magnitude as our primary continuous variable for scoring.
    baseline = get_historical_baseline(
        company_id=company_id,
        signal_type=feature.signal_type,
        metric_name="magnitude",
        repository=repository
    )

    # 2. If there isn't enough history, we cannot mathematically prove an anomaly.
    if not baseline:
        return EvidenceScore(
            feature=feature,
            baseline=None,
            z_score=0.0
        )

    # 3. Calculate how unusual this specific magnitude is
    z_score = calculate_z_score(
        value=feature.magnitude,
        mean_value=baseline.mean,
        std_dev=baseline.standard_deviation
    )

    # 4. Return the fully audited score
    return EvidenceScore(
        feature=feature,
        baseline=baseline,
        z_score=z_score
    )