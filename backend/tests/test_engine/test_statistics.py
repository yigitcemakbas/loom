"""Tests for the statistics package.

The merged version of this engine was inert on every real signal and nothing
caught it, because the failure mode was silence rather than an exception: a
wrong metric name returned None, an empty sample looked like "no history", and
the logs printed a confident 0.00. Several tests here exist specifically to
make that class of failure loud.
"""

from collections import Counter
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.engine.statistics.baseline import (
    MIN_SAMPLES,
    build_baseline,
    count_labels,
)
from app.engine.statistics.engine import ANOMALY_RATE, EvidenceScore, evaluate_evidence
from app.engine.statistics.features import (
    MAGNITUDE_WEIGHT,
    feature_from_signal,
    magnitude_label_of,
    magnitude_of,
)
from app.engine.statistics.statistics import (
    calculate_mean,
    calculate_standard_deviation,
    calculate_variance,
    calculate_z_score,
    rate_lift,
    shrunk_rate,
)
from app.models.signal import SignalType

NOW = datetime(2026, 9, 1, tzinfo=timezone.utc)


def _signal(magnitude=None, *, sid="s1", days_ago=1, direction=None, stype=SignalType.NEW_RISK_FACTOR):
    return SimpleNamespace(
        id=sid,
        signal_type=stype,
        market_magnitude=magnitude,
        market_direction=direction,
        confidence=0.8,
        sentiment_score=None,
        occurred_at=NOW - timedelta(days=days_ago),
    )


# ---- features: the extractor that replaced the metric-name string ----------


def test_magnitude_uses_the_projects_single_weight_table():
    assert magnitude_of(_signal("minor")) == MAGNITUDE_WEIGHT["minor"]
    assert magnitude_of(_signal("moderate")) == MAGNITUDE_WEIGHT["moderate"]
    assert magnitude_of(_signal("major")) == MAGNITUDE_WEIGHT["major"]


def test_unassessed_magnitude_is_none_not_moderate():
    """The bug that would have poisoned every baseline. Just under half the
    stored corpus has no magnitude, and the previous inline expression scored
    all of it "moderate", feeding a large block of identical synthetic values
    into the history that defines what is normal."""
    assert magnitude_of(_signal(None)) is None
    assert magnitude_label_of(_signal(None)) is None


def test_unknown_magnitude_label_is_none():
    assert magnitude_of(_signal("catastrophic")) is None


def test_feature_from_signal_carries_label_and_direction():
    feature = feature_from_signal(_signal("major", direction="negative"))
    assert feature.magnitude == 2.0
    assert feature.magnitude_label == "major"
    assert feature.direction == -1.0


def test_feature_direction_defaults_to_neutral():
    assert feature_from_signal(_signal("minor", direction=None)).direction == 0.0


# ---- counting: absence must stay absent -----------------------------------


def test_count_labels_skips_unassessed_signals():
    counts = count_labels(
        [_signal("major"), _signal(None), _signal("minor"), _signal(None)],
        magnitude_label_of,
    )
    assert counts == Counter({"major": 1, "minor": 1})
    assert sum(counts.values()) == 2, "unassessed findings must not inflate the sample"


# ---- pure statistics -------------------------------------------------------


def test_zero_variance_is_undefined_not_average():
    """Returning 0.0 here reads downstream as "perfectly average", so a company
    whose history is flat would have its first departure from that history
    reported as unremarkable, which is the case the engine exists to catch."""
    assert calculate_z_score(5.0, 1.0, 0.0) is None


def test_z_score_still_works_for_continuous_metrics():
    values = [1.0, 2.0, 3.0, 4.0]
    mean = calculate_mean(values)
    sd = calculate_standard_deviation(calculate_variance(values, mean))
    assert calculate_z_score(mean + sd, mean, sd) == pytest.approx(1.0)


@pytest.mark.parametrize("bad", [([],)])
def test_mean_refuses_empty_input(bad):
    with pytest.raises(ValueError):
        calculate_mean([])


def test_shrunk_rate_with_no_history_is_exactly_the_prior():
    assert shrunk_rate(0, 0, prior_rate=0.08, prior_weight=10.0) == pytest.approx(0.08)


def test_shrunk_rate_pulls_a_thin_sample_toward_the_prior():
    """One "major" in two findings is not evidence that half this company's
    findings are major, which is exactly what an unshrunk rate would claim."""
    raw = 1 / 2
    shrunk = shrunk_rate(1, 2, prior_rate=0.08, prior_weight=10.0)
    assert shrunk < raw
    assert abs(shrunk - 0.08) < abs(raw - 0.08)


def test_shrunk_rate_defers_to_local_history_once_it_is_substantial():
    thin = shrunk_rate(1, 2, prior_rate=0.08, prior_weight=10.0)
    thick = shrunk_rate(50, 100, prior_rate=0.08, prior_weight=10.0)
    assert thick > thin
    # (50 + 10*0.08) / (100 + 10) = 0.462: close to the local 0.5, and the
    # prior's pull has shrunk from decisive to marginal.
    assert thick == pytest.approx(0.462, abs=0.005)


def test_shrunk_rate_rejects_impossible_counts():
    with pytest.raises(ValueError):
        shrunk_rate(3, 2, prior_rate=0.1, prior_weight=10.0)


def test_rate_lift_is_undefined_against_a_zero_reference():
    assert rate_lift(0.2, 0.0) is None
    assert rate_lift(0.2, 0.1) == pytest.approx(2.0)


# ---- baseline construction -------------------------------------------------


def test_baseline_refuses_a_sample_below_the_floor():
    counts = Counter({"moderate": MIN_SAMPLES - 1})
    assert build_baseline(counts, SignalType.NEW_RISK_FACTOR, {"moderate": 0.5}) is None


def test_baseline_reports_counts_alongside_rates_for_auditability():
    counts = Counter({"minor": 5, "moderate": 10, "major": 1})
    baseline = build_baseline(
        counts, SignalType.NEW_RISK_FACTOR,
        {"minor": 0.2, "moderate": 0.7, "major": 0.1},
    )
    assert baseline is not None
    assert baseline.sample_size == 16
    assert baseline.counts == {"minor": 5, "moderate": 10, "major": 1}
    assert baseline.rate_for("major") < baseline.rate_for("moderate")


# ---- the engine ------------------------------------------------------------


class _Repo:
    """Stands in for SignalRepository, and records how often it is queried."""

    def __init__(self, company_signals, population_signals=None):
        self.company_signals = company_signals
        self.population_signals = population_signals or company_signals
        self.baseline_calls = 0
        self.prior_calls = 0

    def list_for_baseline(self, **kwargs):
        self.baseline_calls += 1
        return self.company_signals

    def list_for_global_prior(self, **kwargs):
        self.prior_calls += 1
        return self.population_signals


def _history(n_moderate=14, n_major=1):
    out = []
    for i in range(n_moderate):
        out.append(_signal("moderate", sid=f"m{i}", days_ago=i + 2))
    for i in range(n_major):
        out.append(_signal("major", sid=f"M{i}", days_ago=i + 2))
    return out


def test_engine_scores_a_real_finding():
    """The regression test for the merged version, which produced no baseline
    and a z-score of 0.0 for every one of 229 real signals."""
    subject = _signal("major", sid="subject", days_ago=0)
    repo = _Repo(_history())
    score = evaluate_evidence(
        feature_from_signal(subject), "company-1", repo,
        signal_type=SignalType.NEW_RISK_FACTOR, exclude_signal_id="subject",
    )
    assert score.is_computable, "a company with ample history must produce a score"
    assert score.baseline is not None
    assert score.rate is not None


def test_rare_severity_is_flagged_and_common_severity_is_not():
    repo = _Repo(_history(n_moderate=40, n_major=0))
    rare = evaluate_evidence(
        feature_from_signal(_signal("major", sid="x", days_ago=0)), "c", repo,
        signal_type=SignalType.NEW_RISK_FACTOR, exclude_signal_id="x",
    )
    common = evaluate_evidence(
        feature_from_signal(_signal("moderate", sid="y", days_ago=0)), "c", repo,
        signal_type=SignalType.NEW_RISK_FACTOR, exclude_signal_id="y",
    )
    assert rare.rate < ANOMALY_RATE and rare.is_anomalous
    assert common.rate > ANOMALY_RATE and not common.is_anomalous


def test_unassessed_finding_is_not_scored_at_all():
    repo = _Repo(_history())
    score = evaluate_evidence(
        feature_from_signal(_signal(None, sid="x", days_ago=0)), "c", repo,
        signal_type=SignalType.NEW_RISK_FACTOR,
    )
    assert not score.is_computable
    assert not score.is_anomalous


def test_thin_history_yields_no_verdict_rather_than_a_confident_one():
    repo = _Repo(_history(n_moderate=3, n_major=0))
    score = evaluate_evidence(
        feature_from_signal(_signal("major", sid="x", days_ago=0)), "c", repo,
        signal_type=SignalType.NEW_RISK_FACTOR, exclude_signal_id="x",
    )
    assert not score.is_computable
    assert not score.is_anomalous, "absence of evidence must not read as an anomaly"


def test_not_anomalous_is_distinguishable_from_not_computable():
    """`is_anomalous` is False in both cases, so a caller that cannot tell them
    apart would report a company with no history as calm."""
    repo = _Repo(_history(n_moderate=40, n_major=0))
    calm = evaluate_evidence(
        feature_from_signal(_signal("moderate", sid="y", days_ago=0)), "c", repo,
        signal_type=SignalType.NEW_RISK_FACTOR, exclude_signal_id="y",
    )
    unknown = evaluate_evidence(
        feature_from_signal(_signal(None, sid="z", days_ago=0)), "c", repo,
        signal_type=SignalType.NEW_RISK_FACTOR,
    )
    assert calm.is_computable and not calm.is_anomalous
    assert not unknown.is_computable and not unknown.is_anomalous


def test_scored_signal_is_excluded_from_its_own_baseline():
    subject = _signal("major", sid="subject", days_ago=0)
    history = _history(n_moderate=14, n_major=0) + [subject]
    repo = _Repo(history)
    score = evaluate_evidence(
        feature_from_signal(subject), "c", repo,
        signal_type=SignalType.NEW_RISK_FACTOR, exclude_signal_id="subject",
    )
    assert score.baseline.counts.get("major", 0) == 0, (
        "the observation under evaluation must not appear in the history it is judged against"
    )


def test_baseline_is_fetched_once_per_company_and_type():
    """The first version of the cache keyed on the excluded signal id, which is
    unique per finding, so it never hit and issued more queries than no cache."""
    from app.engine.statistics.engine import BaselineCache

    repo = _Repo(_history())
    cache = BaselineCache(repo)
    for i in range(25):
        evaluate_evidence(
            feature_from_signal(_signal("major", sid=f"s{i}", days_ago=0)), "c", repo,
            signal_type=SignalType.NEW_RISK_FACTOR, exclude_signal_id=f"s{i}", cache=cache,
        )
    assert repo.baseline_calls == 1
    assert repo.prior_calls == 1
