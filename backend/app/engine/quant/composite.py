"""Folding many factor ranks into one number, and refusing to when it would lie.

A composite is the most dangerous object in this package. It reads as a verdict,
it is trivial to produce, and it hides every weakness in its inputs: a score of
0.82 built from two factors looks exactly like a score of 0.82 built from ten,
and a reader has no way to tell them apart from the number. So the rules here
are mostly about when NOT to answer.

Equal weighting, on purpose. Fitting weights to this universe would produce a
number that describes the last eighteen months of a hundred and twenty
companies and nothing else, and it would look more precise for it. Equal
weights are the honest default when there is no out-of-sample evidence to tune
against, and Loom does not have one yet.
"""

from dataclasses import dataclass, field
from typing import Optional

from app.engine.quant.crosssection import Ranked
from app.engine.quant.factors import FACTORS_BY_KEY

# Below this many factors, no composite is produced at all. Four is already
# generous: it means a third of the library, and a reader is being handed one
# number in place of the ten they think it summarises.
MIN_FACTORS_FOR_COMPOSITE = 4

# The Piotroski tests this database can actually run. The original has nine;
# two of them need a current assets and current liabilities split that the
# ingest does not collect, so the score is reported out of seven with the
# denominator stated rather than out of nine with two silently failed.
F_SCORE_TESTS = (
    ("return_on_assets", lambda v: v > 0),
    ("cash_conversion", lambda v: v > 1.0),
    ("accruals", lambda v: v < 0),
    ("leverage_change", lambda v: v <= 0),
    ("operating_margin_change", lambda v: v > 0),
    ("asset_turnover_change", lambda v: v > 0),
    ("net_share_issuance", lambda v: v <= 0.0),
)


@dataclass(frozen=True)
class Composite:
    # 0.0 to 1.0, the mean of the factor percentiles, already oriented so high
    # is good.
    score: float
    # How many factors went into it. Shown everywhere the score is shown,
    # because it is the difference between a judgement and an impression.
    factor_count: int
    # Which ones, so the number can be taken apart.
    factors_used: list[str] = field(default_factory=list)
    # Factors in the top or bottom decile of the universe, the readings that
    # actually justify surfacing this company to a reader.
    extremes: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class FScore:
    """Piotroski's fundamental health score, over the tests we can run."""

    passed: int
    available: int
    failed_tests: list[str] = field(default_factory=list)
    passed_tests: list[str] = field(default_factory=list)

    @property
    def fraction(self) -> Optional[float]:
        return self.passed / self.available if self.available else None


def build_composite(ranked: dict[str, Ranked]) -> Optional[Composite]:
    """Average the percentiles, or decline if too few factors survived.

    Returns None rather than a low-confidence number. A composite built from
    three factors is not a weak signal to be discounted later; it is a
    different measurement wearing the same name, and callers reliably forget to
    discount it.
    """
    usable = {k: r for k, r in ranked.items() if k in FACTORS_BY_KEY}
    if len(usable) < MIN_FACTORS_FOR_COMPOSITE:
        return None

    score = sum(r.percentile for r in usable.values()) / len(usable)
    return Composite(
        score=round(score, 4),
        factor_count=len(usable),
        factors_used=sorted(usable),
        extremes=sorted(k for k, r in usable.items() if r.is_extreme),
    )


def build_f_score(values: dict[str, float]) -> FScore:
    """Run the health tests whose inputs are present.

    `available` is the count of tests that could be run, never the count that
    passed. Scoring a company 4 out of 7 when only four tests had data would
    report a failing company, and the failure would be the database's.
    """
    passed: list[str] = []
    failed: list[str] = []
    for key, test in F_SCORE_TESTS:
        if key not in values:
            continue
        (passed if test(values[key]) else failed).append(key)
    return FScore(
        passed=len(passed),
        available=len(passed) + len(failed),
        failed_tests=failed,
        passed_tests=passed,
    )


def composite_phrase(composite: Optional[Composite]) -> str:
    """What the number means, in words, with its own weakness attached."""
    if composite is None:
        return "Not enough reported figures to score this company."
    if composite.score >= 0.75:
        band = "The reported numbers are stronger than most of Loom's universe"
    elif composite.score >= 0.55:
        band = "The reported numbers are a little better than average"
    elif composite.score >= 0.45:
        band = "The reported numbers are about average"
    elif composite.score >= 0.25:
        band = "The reported numbers are weaker than most of Loom's universe"
    else:
        band = "The reported numbers are among the weakest in Loom's universe"
    return f"{band}, on {composite.factor_count} measures."


__all__ = [
    "F_SCORE_TESTS",
    "MIN_FACTORS_FOR_COMPOSITE",
    "Composite",
    "FScore",
    "build_composite",
    "build_f_score",
    "composite_phrase",
]
