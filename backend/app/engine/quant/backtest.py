"""Did the factors rank returns? The measurement Loom has never made.

Everything else in the quant package forms an opinion about a company. This
module asks whether those opinions were worth anything, over six years of
prices and roughly sixty rebalances, which is the first sample in this project
large enough for the answer to mean something.

Four decisions here matter more than the arithmetic.

**Fama-MacBeth, not pooled.** Every company in one month shares that month's
market. Pooling seven thousand company-months and running one t-test treats
them as seven thousand independent observations when they are closer to sixty,
and it shrinks the standard error by roughly an order of magnitude. So the
spread is computed WITHIN each month, and the t-test runs on the time series of
monthly spreads. This is the difference between a result and an artefact.

**The primary statistic is a long-short spread, because of survivorship.** The
universe is today's index members, so every company in it survived; everything
that was delisted, acquired at a discount or went to zero is missing. A
long-only return measured against a benchmark is therefore contaminated
upward, badly. A spread between the top and bottom of the same universe is far
more robust: both legs are drawn from survivors, so the bias largely cancels.
Long-only numbers are still reported, and reported as contaminated.

**Holding periods do not overlap.** A monthly rebalance held one month
produces independent observations. Holding three months while rebalancing
monthly would triple-count every return and inflate every t-statistic, which
is the same mistake as pooling wearing different clothes.

**Every factor is a trial.** Seventeen factors plus a composite is eighteen
chances to find something, and with eighteen tries a result that would be
surprising once is close to expected. The Bonferroni threshold is printed
beside the results rather than left to the reader's discipline.

Nothing here is a trading strategy. There are no transaction costs, no
position limits, no borrow costs on the short leg, and no slippage. A spread
that survives everything below is a reason to keep looking, not a reason to
trade.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.evaluation import spearman
from app.engine.quant.composite import build_composite
from app.engine.quant.relevance import weighted_composite
from app.engine.quant.factors import FACTORS
from app.engine.quant.prices import PriceHistory, history_from_bars
from app.engine.quant.runner import score_universe
from app.models.company import Company
from app.models.factor import COMPOSITE_KEY
from app.models.price_bar import PriceBar

logger = logging.getLogger(__name__)

# Quintiles rather than deciles. With roughly a hundred and twenty scorable
# companies a decile holds twelve names, and a twelve-stock portfolio's return
# is dominated by whichever two names moved most. Quintiles hold about
# twenty-four, which is still thin and is the most this universe supports.
QUANTILES = 5

# Both legs must have this many names or the month is skipped for that factor.
# A "top quintile" of four companies is not a portfolio.
MIN_LEG = 5

# Rebalance cadence in days, and the holding period, deliberately equal so that
# consecutive observations do not overlap.
REBALANCE_DAYS = 30

# The conditional composite is scored under its own key so the two can be
# compared in one run rather than across two runs of different windows.
CONDITIONAL_KEY = "composite_conditional"


@dataclass
class MonthResult:
    """One rebalance for one factor."""

    as_of: date
    long_return: float
    short_return: float
    universe_return: float
    benchmark_return: Optional[float]
    long_names: int
    short_names: int
    # Rank correlation between the factor percentile and the forward return,
    # across every scored company that month. Uses the whole cross-section
    # rather than just the extremes, so it notices a factor that orders the
    # middle correctly while the tails are noise.
    information_coefficient: Optional[float]

    @property
    def spread(self) -> float:
        return self.long_return - self.short_return

    @property
    def long_excess(self) -> Optional[float]:
        """Long leg against the benchmark. Survivorship-contaminated, and the
        contamination is in this number rather than in the spread."""
        if self.benchmark_return is None:
            return None
        return self.long_return - self.benchmark_return


@dataclass
class FactorResult:
    key: str
    label: str
    months: list[MonthResult] = field(default_factory=list)

    @property
    def spreads(self) -> list[float]:
        return [m.spread for m in self.months]

    @property
    def mean_spread(self) -> Optional[float]:
        return _mean(self.spreads)

    @property
    def t_statistic(self) -> Optional[float]:
        """Fama-MacBeth t on the time series of monthly spreads.

        One observation per rebalance, not per company. The whole point of
        computing the spread within the month first is that this t-statistic
        then has the degrees of freedom it claims.
        """
        return _t_of_mean(self.spreads)

    @property
    def hit_rate(self) -> Optional[float]:
        """Share of months the spread was positive. A mean can be carried by
        two months out of sixty; this says whether it was."""
        if not self.spreads:
            return None
        return sum(1 for s in self.spreads if s > 0) / len(self.spreads)

    @property
    def mean_information_coefficient(self) -> Optional[float]:
        values = [m.information_coefficient for m in self.months if m.information_coefficient is not None]
        return _mean(values)

    @property
    def mean_long_excess(self) -> Optional[float]:
        values = [m.long_excess for m in self.months if m.long_excess is not None]
        return _mean(values)


@dataclass
class SurvivorshipReport:
    """How much of any apparent edge is the universe rather than the factors.

    The universe was seeded from a current index membership list, so every
    company in it survived to today. Nothing that was delisted, acquired at a
    discount, or went to zero is present, and a company that joined the index
    in 2024 because it had done well is in the 2021 backtest.

    This cannot be repaired without a historical membership list, which is not
    freely available. What it can be is measured: if the universe's own average
    return beat the benchmark substantially, that gap is the size of the free
    lunch the construction handed every long-only number in this report.
    """

    months: int
    mean_universe_return: Optional[float]
    mean_benchmark_return: Optional[float]
    companies: int
    # Companies whose price history begins after the backtest did. These are
    # late entrants rather than survivors, and they are a milder problem: a
    # listing date is knowable at the time, unlike index membership in 2024.
    late_entrants: int

    @property
    def excess(self) -> Optional[float]:
        if self.mean_universe_return is None or self.mean_benchmark_return is None:
            return None
        return self.mean_universe_return - self.mean_benchmark_return


@dataclass
class BacktestResult:
    start: date
    end: date
    rebalances: int
    factors: list[FactorResult]
    survivorship: SurvivorshipReport


def _mean(values: Sequence[float]) -> Optional[float]:
    return sum(values) / len(values) if values else None


def _t_of_mean(values: Sequence[float]) -> Optional[float]:
    """One-sample t against zero. Returns None below a sample that could not
    support the claim regardless of what the arithmetic produced."""
    if len(values) < 8:
        return None
    mean = sum(values) / len(values)
    variance = sum((v - mean) ** 2 for v in values) / (len(values) - 1)
    if variance <= 0:
        return None
    standard_error = (variance / len(values)) ** 0.5
    if standard_error == 0:
        return None
    return mean / standard_error


def rebalance_dates(start: date, end: date, step_days: int = REBALANCE_DAYS) -> list[date]:
    """Evenly spaced dates, each one a formation date whose return is measured
    to the next. The last is dropped because it has no forward window."""
    dates: list[date] = []
    current = start
    while current <= end:
        dates.append(current)
        current += timedelta(days=step_days)
    return dates[:-1] if len(dates) > 1 else []


def _load_prices(db: Session) -> tuple[dict[str, PriceHistory], Optional[PriceHistory]]:
    """Every stored bar in one query, split by ticker, benchmark separated."""
    rows = db.execute(
        select(PriceBar, Company.ticker).join(Company, Company.id == PriceBar.company_id)
    ).all()

    grouped: dict[str, list] = {}
    for bar, ticker in rows:
        grouped.setdefault(ticker, []).append(bar)

    histories = {t: history_from_bars(bars) for t, bars in grouped.items()}
    benchmark = histories.pop("QQQ", None)
    return histories, benchmark


def run_backtest(
    db: Session,
    *,
    start: date,
    end: date,
    step_days: int = REBALANCE_DAYS,
    quantiles: int = QUANTILES,
) -> BacktestResult:
    """Score the universe at every rebalance and measure what happened next."""
    histories, benchmark = _load_prices(db)
    dates = rebalance_dates(start, end, step_days)
    if not dates:
        raise ValueError("The window is too short to contain a single rebalance.")

    results: dict[str, FactorResult] = {
        f.key: FactorResult(key=f.key, label=f.label) for f in FACTORS
    }
    # Both composites, measured side by side. The flat mean is what the first
    # backtest scored at t=0.40 over fifty-five periods; the conditional one
    # averages within themes and switches factors off where the economics say
    # they do not describe the company. Running them together is the only way
    # to know whether the second is an improvement rather than a preference.
    results[COMPOSITE_KEY] = FactorResult(key=COMPOSITE_KEY, label="Composite (flat)")
    results[CONDITIONAL_KEY] = FactorResult(key=CONDITIONAL_KEY, label="Composite (conditional)")

    universe_returns: list[float] = []
    benchmark_returns: list[float] = []
    seen_companies: set[str] = set()

    for formation in dates:
        horizon = formation + timedelta(days=step_days)

        scores = score_universe(db, as_of=formation)
        if not scores.raw:
            continue

        # The forward return every company in this month is judged on. Taken
        # from adjusted closes, because a split inside the window would
        # otherwise read as a fifty percent loss.
        forward: dict[str, float] = {}
        for ticker in scores.raw:
            history = histories.get(ticker)
            if history is None:
                continue
            value = history.total_return(formation, horizon)
            if value is not None:
                forward[ticker] = value
        if len(forward) < quantiles * MIN_LEG:
            continue

        seen_companies.update(forward)
        universe_mean = _mean(list(forward.values()))
        bench = benchmark.total_return(formation, horizon) if benchmark else None
        if universe_mean is not None:
            universe_returns.append(universe_mean)
        if bench is not None:
            benchmark_returns.append(bench)

        # Percentiles per factor, plus the folded composite, all already
        # oriented so that high is good.
        percentiles: dict[str, dict[str, float]] = {}
        for ticker, ranks in scores.ranked.items():
            if ticker not in forward:
                continue
            for key, rank in ranks.items():
                percentiles.setdefault(key, {})[ticker] = rank.percentile
            composite = build_composite(ranks)
            if composite is not None:
                percentiles.setdefault(COMPOSITE_KEY, {})[ticker] = composite.score
            conditional = weighted_composite(
                {k: r.percentile for k, r in ranks.items()},
                sector=scores.sector.get(ticker),
            )
            if conditional is not None:
                percentiles.setdefault(CONDITIONAL_KEY, {})[ticker] = conditional.score

        for key, by_ticker in percentiles.items():
            result = results.get(key)
            if result is None:
                continue
            month = _measure(
                as_of=formation,
                percentiles=by_ticker,
                forward=forward,
                universe_return=universe_mean or 0.0,
                benchmark_return=bench,
                quantiles=quantiles,
            )
            if month is not None:
                result.months.append(month)

    late = sum(
        1 for ticker in seen_companies
        if (h := histories.get(ticker)) is not None
        and h.on_or_before(start) is None
    )

    return BacktestResult(
        start=start, end=end, rebalances=len(dates),
        factors=[r for r in results.values() if r.months],
        survivorship=SurvivorshipReport(
            months=len(universe_returns),
            mean_universe_return=_mean(universe_returns),
            mean_benchmark_return=_mean(benchmark_returns),
            companies=len(seen_companies),
            late_entrants=late,
        ),
    )


def _measure(
    *,
    as_of: date,
    percentiles: dict[str, float],
    forward: dict[str, float],
    universe_return: float,
    benchmark_return: Optional[float],
    quantiles: int,
) -> Optional[MonthResult]:
    """One factor, one month: sort, split, and measure both ends."""
    ranked = sorted(percentiles.items(), key=lambda kv: kv[1])
    if len(ranked) < quantiles * MIN_LEG:
        return None

    size = len(ranked) // quantiles
    if size < MIN_LEG:
        return None

    short_leg = [t for t, _ in ranked[:size]]          # worst percentiles
    long_leg = [t for t, _ in ranked[-size:]]          # best percentiles

    long_return = _mean([forward[t] for t in long_leg if t in forward])
    short_return = _mean([forward[t] for t in short_leg if t in forward])
    if long_return is None or short_return is None:
        return None

    shared = [t for t, _ in ranked if t in forward]
    ic = spearman([percentiles[t] for t in shared], [forward[t] for t in shared])

    return MonthResult(
        as_of=as_of,
        long_return=long_return,
        short_return=short_return,
        universe_return=universe_return,
        benchmark_return=benchmark_return,
        long_names=len(long_leg),
        short_names=len(short_leg),
        information_coefficient=ic,
    )


__all__ = [
    "MIN_LEG",
    "QUANTILES",
    "CONDITIONAL_KEY",
    "REBALANCE_DAYS",
    "BacktestResult",
    "FactorResult",
    "MonthResult",
    "SurvivorshipReport",
    "rebalance_dates",
    "run_backtest",
]
