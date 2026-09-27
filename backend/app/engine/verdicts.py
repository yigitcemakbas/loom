"""Was Loom right? Scored by component, so the answer is useful.

Loom has issued dated verdicts since August and has never checked one. The
signal evaluator scores individual findings and the factor backtest scores
individual measures; nothing has ever scored the thing the product actually
puts in front of a person.

**The component split is the point.** Scoring a blended verdict would answer
"is Loom right" with a single number, which is the least useful shape the
answer could take: a null tells you nothing about what to fix, and a positive
tells you nothing about what to keep. Loom has two halves that reach
conclusions independently, and their track records are almost certainly
different:

  read     what the language models extracted from filings and transcripts
  numbers  the condition-weighted themes over filed accounts

Measured separately, a null on one and a signal on the other is actionable.
Measured together it is one more inconclusive t-statistic.

Four things here are methodology rather than preference.

**One verdict per company per day.** Briefs are regenerated on a schedule, so a
company can carry nine of them in a month saying the same thing. Counting each
would inflate the sample severalfold and shrink every confidence interval to
match.

**Entry strictly after the verdict.** A brief generated during a session cannot
be traded at that session's close.

**Abnormal, not raw.** A stock that fell three percent on a day the market fell
three percent has told you nothing, and counting that as a correct bearish call
is the most common way a strategy looks profitable on paper.

**The numbers read is recomputed point-in-time, never read from storage.**
Factor scores are stored for the day they were computed; reconstructing what
Loom would have said on 3 September means scoring that day's universe from
filings filed by then, which is what the backtest machinery already does.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.evaluation import (
    BENCHMARK_TICKER,
    _PriceCache,
    forward_return,
    multiple_testing_threshold,
    spearman,
)
from app.models.brief import CompanyBrief, Stance
from app.models.company import Company

logger = logging.getLogger(__name__)

# Which stances count as a directional call. Mixed, quiet and insufficient are
# Loom declining to answer, and scoring a refusal as a wrong answer would
# punish the engine for its own honesty.
_DIRECTION = {
    Stance.STRONG_POSITIVE: 1.0,
    Stance.POSITIVE: 1.0,
    Stance.NEGATIVE: -1.0,
    Stance.STRONG_NEGATIVE: -1.0,
}

# Strength, for the rank correlation. A "clearly negative" that is wrong should
# cost more than a "leaning negative" that is wrong.
_STRENGTH = {
    Stance.STRONG_POSITIVE: 1.0,
    Stance.POSITIVE: 0.5,
    Stance.NEGATIVE: -0.5,
    Stance.STRONG_NEGATIVE: -1.0,
}

# Where the numbers read splits into a call. A company in the top third of its
# peers on the themes is a positive call, the bottom third negative, and the
# middle is Loom declining to answer, which mirrors how the stance treats its
# own middle.
NUMBERS_POSITIVE = 0.60
NUMBERS_NEGATIVE = 0.40

# Holding periods, in sessions. A month is included because the verdict is
# built from quarterly disclosures and a one-day horizon asks it to do
# something it was never designed for.
HORIZONS = (1, 5, 21)


@dataclass
class Call:
    """One directional judgement, ready to be scored."""

    ticker: str
    made_on: datetime
    direction: float
    strength: float
    component: str          # "read" | "numbers"
    abnormal_return: Optional[float] = None


@dataclass
class ComponentResult:
    component: str
    horizon: int
    calls: int = 0
    skipped: int = 0
    hit_rate: Optional[float] = None
    positive_mean: Optional[float] = None
    negative_mean: Optional[float] = None
    spread: Optional[float] = None
    t_statistic: Optional[float] = None
    information_coefficient: Optional[float] = None

    def verdict(self) -> str:
        if self.calls < 30:
            return (
                f"{self.calls} calls is too few to conclude anything. "
                f"At least 30 are needed before these numbers mean much."
            )
        if self.t_statistic is None or abs(self.t_statistic) < 2.0:
            return (
                f"Spread of {(self.spread or 0):+.2f}%, t={self.t_statistic:+.2f}. "
                f"Inside the range chance produces."
            )
        return (
            f"Spread of {(self.spread or 0):+.2f}%, t={self.t_statistic:+.2f}. "
            f"Worth investigating, not yet worth trading."
        )


@dataclass
class VerdictReport:
    results: list[ComponentResult] = field(default_factory=list)
    threshold: float = 0.0

    def survivors(self) -> list[ComponentResult]:
        return [
            r for r in self.results
            if r.calls >= 30 and r.t_statistic is not None
            and abs(r.t_statistic) >= self.threshold
        ]


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def read_calls(db: Session, *, limit: int = 4000) -> list[Call]:
    """Loom's own verdicts, one per company per day.

    The newest brief on a day wins, because it is the one a reader would
    actually have seen.
    """
    rows = db.execute(
        select(CompanyBrief, Company.ticker)
        .join(Company, Company.id == CompanyBrief.company_id)
        .order_by(CompanyBrief.generated_at)
        .limit(limit)
    ).all()

    by_day: dict[tuple[str, date], Call] = {}
    for brief, ticker in rows:
        direction = _DIRECTION.get(brief.stance)
        if direction is None:
            continue
        made = _aware(brief.generated_at)
        by_day[(ticker, made.date())] = Call(
            ticker=ticker, made_on=made, direction=direction,
            strength=_STRENGTH.get(brief.stance, 0.0), component="read",
        )
    return list(by_day.values())


def numbers_calls(db: Session, on_dates: Sequence[date]) -> list[Call]:
    """What the accounts said on the same days, reconstructed point-in-time.

    Recomputed rather than read from storage: factor scores are kept for the
    day they were computed, and the question here is what Loom would have said
    on a past date using only what had been filed by then.
    """
    from app.engine.quant.relevance import weighted_composite
    from app.engine.quant.runner import score_universe

    calls: list[Call] = []
    for when in sorted(set(on_dates)):
        try:
            scores = score_universe(db, as_of=when)
        except Exception:
            logger.exception("Could not score the universe as of %s", when)
            continue

        for ticker, ranks in scores.ranked.items():
            composite = weighted_composite(
                {k: r.percentile for k, r in ranks.items()},
                sector=scores.sector.get(ticker),
            )
            if composite is None:
                continue
            if composite.score >= NUMBERS_POSITIVE:
                direction, strength = 1.0, composite.score
            elif composite.score <= NUMBERS_NEGATIVE:
                direction, strength = -1.0, composite.score - 1.0
            else:
                # The middle is the numbers declining to answer, mirroring how
                # the stance treats its own middle.
                continue
            calls.append(Call(
                ticker=ticker,
                made_on=datetime.combine(when, datetime.min.time(), tzinfo=timezone.utc),
                direction=direction, strength=strength, component="numbers",
            ))
    return calls


def score(db: Session, calls: list[Call], horizon: int) -> ComponentResult:
    """Measure one component at one horizon."""
    cache = _PriceCache(db)
    benchmark = cache.get(BENCHMARK_TICKER)
    component = calls[0].component if calls else "read"
    result = ComponentResult(component=component, horizon=horizon)

    scored: list[Call] = []
    for call in calls:
        stock = forward_return(cache.get(call.ticker), call.made_on, horizon)
        if stock is None:
            result.skipped += 1
            continue
        bench = forward_return(benchmark, call.made_on, horizon)
        if bench is None:
            result.skipped += 1
            continue
        call.abnormal_return = round(stock[0] - bench[0], 4)
        scored.append(call)

    if not scored:
        return result

    positives = [c.abnormal_return for c in scored if c.direction > 0]
    negatives = [c.abnormal_return for c in scored if c.direction < 0]
    result.calls = len(scored)
    result.hit_rate = sum(
        1 for c in scored if (c.abnormal_return or 0) * c.direction > 0
    ) / len(scored)
    result.positive_mean = _mean(positives)
    result.negative_mean = _mean(negatives)
    if result.positive_mean is not None and result.negative_mean is not None:
        result.spread = round(result.positive_mean - result.negative_mean, 4)
        result.t_statistic = _welch_t(positives, negatives)
    result.information_coefficient = spearman(
        [c.strength for c in scored], [c.abnormal_return or 0.0 for c in scored]
    )
    return result


def evaluate_verdicts(db: Session, *, horizons: Sequence[int] = HORIZONS) -> VerdictReport:
    """Score both halves of Loom, separately, at every horizon."""
    read = read_calls(db)
    numbers = numbers_calls(db, [c.made_on.date() for c in read])

    report = VerdictReport()
    for horizon in horizons:
        if read:
            report.results.append(score(db, list(read), horizon))
        if numbers:
            report.results.append(score(db, list(numbers), horizon))

    # Every component at every horizon is a separate chance to be lucky.
    report.threshold = multiple_testing_threshold(max(1, len(report.results)))
    return report


def _mean(values: Sequence[float]) -> Optional[float]:
    return round(sum(values) / len(values), 4) if values else None


def _welch_t(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    import math

    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    denom = math.sqrt(va / len(a) + vb / len(b))
    return round((ma - mb) / denom, 3) if denom else None


__all__ = [
    "HORIZONS",
    "NUMBERS_NEGATIVE",
    "NUMBERS_POSITIVE",
    "Call",
    "ComponentResult",
    "VerdictReport",
    "evaluate_verdicts",
    "numbers_calls",
    "read_calls",
    "score",
]
