"""Did any of it work? The measurement loop.

Loom has produced confident directional judgements for close to a year and has
never once checked one against what the price subsequently did. Everything
else in the engine is machinery for forming an opinion; this is the only module
that asks whether the opinions are worth anything.

Three decisions here matter more than the arithmetic.

**Returns are measured against a benchmark, not raw.** A stock that falls three
percent on a day the whole market fell three percent has told you nothing, and
counting that as a correct bearish call is the most common way a strategy looks
profitable on paper and is not. What is measured is the abnormal return: the
stock's move minus the benchmark's over the identical window. This is the
market-adjusted model, the simplest honest choice; it assumes a beta of one,
which is wrong in a way that matters more for volatile names than stable ones,
and a full market model estimating beta per company is the obvious next
refinement.

**Entry is strictly after the event.** A judgement timestamped during a session
cannot be traded at that session's close, so the entry price is the close of
the first session that begins after the timestamp. Measuring from the close of
the session containing the event would quietly credit the engine with a move
that had already happened, which is lookahead bias in its purest form and would
inflate every number here.

**Observations are clustered by event, not counted per finding.** One filing
routinely yields half a dozen findings, and they are not six independent tests
of anything: they read the same document about the same company on the same
day, and they move together. Treating them as independent inflates the sample
roughly sixfold and shrinks every confidence interval accordingly, which is how
a null result gets published as a discovery. Findings are therefore folded into
one directional call per company per day before anything is measured.

**The control group is the point.** Knowing that bearish calls preceded a
decline is worth nothing on its own, because the sample might simply be drawn
from a falling market. What matters is the spread between the bullish and
bearish groups, and whether either differs from the calls the engine declined
to make. If findings the engine rated neutral move exactly as much as the ones
it rated major, there is no signal, however impressive the headline number.

**Testing several horizons is itself a hazard.** Sweeping one through six
holding periods and reporting the best is the most reliable way to manufacture
a discovery from noise: with six tries, a result that would be surprising once
is close to expected. `multiple_testing_threshold` states what a t-statistic
has to clear once the number of attempts is accounted for, and the sweep prints
it alongside the results rather than leaving it to the reader's discipline.

Nothing here is a backtest of a trading strategy. There are no transaction
costs, no position sizing, no capacity limits, and at these sample sizes no
result should be treated as more than a reason to keep looking.
"""

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy.orm import Session

from app.ingestion.prices import PriceSeries, get_price_source
from app.repositories.assessment_repository import AssessmentRepository
from app.repositories.company_repository import CompanyRepository
from app.repositories.signal_repository import SignalRepository

logger = logging.getLogger(__name__)

# What the stock is measured against. An index fund rather than a factor model:
# it is the benchmark already tracked and it removes the large majority of the
# shared movement that would otherwise be credited to the engine.
BENCHMARK_TICKER = "QQQ"

# Holding periods worth reporting. One session is the short-horizon claim; five
# tests whether anything persists past the initial reaction.
DEFAULT_HORIZONS = (1, 2, 5)

# Below this many directional calls, report the numbers but refuse to
# characterise them. A hit rate computed from nine observations is noise
# wearing a percentage sign.
MIN_SAMPLE_FOR_VERDICT = 30


@dataclass
class Outcome:
    """One judgement, and what the price did next."""

    subject_id: str
    ticker: str
    occurred_at: datetime
    predicted_direction: str          # positive | negative | neutral
    strength: float                   # priority or score, used for ranking
    forward_return_pct: Optional[float] = None
    benchmark_return_pct: Optional[float] = None
    abnormal_return_pct: Optional[float] = None
    sessions: int = 0

    @property
    def is_directional(self) -> bool:
        return self.predicted_direction in ("positive", "negative")

    @property
    def correct(self) -> Optional[bool]:
        """Whether the abnormal move went the way the call implied."""
        if self.abnormal_return_pct is None or not self.is_directional:
            return None
        if self.predicted_direction == "positive":
            return self.abnormal_return_pct > 0
        return self.abnormal_return_pct < 0

    @property
    def signed_strength(self) -> float:
        if self.predicted_direction == "positive":
            return self.strength
        if self.predicted_direction == "negative":
            return -self.strength
        return 0.0


@dataclass
class EvaluationReport:
    subject: str
    horizon_sessions: int
    evaluated: int
    skipped_no_prices: int
    directional: int
    hit_rate: Optional[float] = None
    # What a caller with no skill would score by always predicting whichever
    # direction happened to dominate the period. A hit rate below this is worse
    # than useless, and one just above it is not an edge.
    baseline_hit_rate: Optional[float] = None
    mean_abnormal_positive: Optional[float] = None
    mean_abnormal_negative: Optional[float] = None
    mean_abnormal_neutral: Optional[float] = None
    spread: Optional[float] = None
    t_statistic: Optional[float] = None
    information_coefficient: Optional[float] = None
    by_ticker: dict = field(default_factory=dict)

    @property
    def has_enough_data(self) -> bool:
        return self.directional >= MIN_SAMPLE_FOR_VERDICT

    @property
    def edge_over_baseline(self) -> Optional[float]:
        if self.hit_rate is None or self.baseline_hit_rate is None:
            return None
        return round(self.hit_rate - self.baseline_hit_rate, 4)

    def verdict(self) -> str:
        """A sentence that refuses to overclaim."""
        if not self.has_enough_data:
            return (
                f"{self.directional} directional calls is too few to conclude anything. "
                f"At least {MIN_SAMPLE_FOR_VERDICT} are needed before these numbers mean much."
            )
        if self.spread is None:
            return "No spread could be computed."
        if self.t_statistic is None:
            # One side of the comparison had fewer than two observations, so
            # the spread is a difference of means with no dispersion behind it.
            return (
                f"Spread of {self.spread:+.2f}%, but one side has too few "
                f"observations to say whether that is anything."
            )
        if abs(self.t_statistic) < 2.0:
            return (
                f"Spread of {self.spread:+.2f}% between bullish and bearish calls, "
                f"but t={self.t_statistic:.2f} is inside the range chance produces. "
                f"No evidence of predictive power."
            )
        direction = "in the predicted direction" if self.spread > 0 else "opposite to the prediction"
        return (
            f"Spread of {self.spread:+.2f}% {direction}, t={self.t_statistic:.2f}. "
            f"Worth investigating further, not yet worth trading."
        )


def _session_index_after(points, cutoff_ts: float) -> Optional[int]:
    """First session that begins strictly after the event.

    Strictly after, because a judgement timestamped inside a session could not
    have been acted on at that session's close.
    """
    for i, point in enumerate(points):
        if point.t > cutoff_ts:
            return i
    return None


def forward_return(
    series: Optional[PriceSeries], occurred_at: datetime, sessions: int
) -> Optional[tuple[float, float, float]]:
    """Return (pct_change, entry_ts, exit_ts) over `sessions` sessions after the event."""
    if series is None or len(series.points) < 2:
        return None

    when = occurred_at if occurred_at.tzinfo else occurred_at.replace(tzinfo=timezone.utc)
    start = _session_index_after(series.points, when.timestamp())
    if start is None:
        return None

    end = start + sessions
    if end >= len(series.points):
        # The window runs past the end of the data. Reporting a shorter window
        # as if it were the requested one would mix horizons silently.
        return None

    entry, exit_ = series.points[start].c, series.points[end].c
    if not entry:
        return None
    return (round((exit_ / entry - 1) * 100, 4), series.points[start].t, series.points[end].t)


def spearman(xs: Sequence[float], ys: Sequence[float]) -> Optional[float]:
    """Rank correlation, implemented here rather than pulled in.

    Rank rather than Pearson because the engine's strength scores are ordinal:
    a priority of 0.9 is not twice as confident as 0.45, it is merely ranked
    above it, and Pearson would take the spacing seriously.
    """
    if len(xs) != len(ys) or len(xs) < 3:
        return None

    def rank(values: Sequence[float]) -> list[float]:
        order = sorted(range(len(values)), key=lambda i: values[i])
        ranks = [0.0] * len(values)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and values[order[j + 1]] == values[order[i]]:
                j += 1
            # Ties share the average of the ranks they span, otherwise a field
            # of equal scores would produce an arbitrary ordering.
            shared = (i + j) / 2 + 1
            for k in range(i, j + 1):
                ranks[order[k]] = shared
            i = j + 1
        return ranks

    rx, ry = rank(xs), rank(ys)
    n = len(rx)
    mx, my = sum(rx) / n, sum(ry) / n
    num = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    denom = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    if denom == 0:
        return None
    return round(num / denom, 4)


def _mean(values: Sequence[float]) -> Optional[float]:
    return round(sum(values) / len(values), 4) if values else None


def _welch_t(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    """Two-sample t with unequal variances, for the bullish/bearish spread.

    Welch rather than Student because there is no reason the two groups should
    have equal variance, and usually they do not.
    """
    if len(a) < 2 or len(b) < 2:
        return None
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    denom = math.sqrt(va / len(a) + vb / len(b))
    if denom == 0:
        return None
    return round((ma - mb) / denom, 3)


def cluster_by_event(outcomes: list[Outcome]) -> list[Outcome]:
    """Fold many findings about one company on one day into a single call.

    Six findings extracted from one filing are one observation, not six. They
    share a document, a company and a day, so their forward returns are
    identical by construction and their directions are highly correlated. Left
    unclustered they multiply the apparent sample size without adding
    information, which makes a t-statistic look roughly two and a half times
    more convincing than the evidence supports.

    Direction is resolved by priority-weighted vote, matching how `brief.py`
    folds a stance, so the clustered call is the one the product would actually
    have shown a reader.
    """
    groups: dict[tuple, list[Outcome]] = {}
    for outcome in outcomes:
        groups.setdefault((outcome.ticker, outcome.occurred_at.date()), []).append(outcome)

    clustered: list[Outcome] = []
    for (ticker, day), members in groups.items():
        weighted = 0.0
        total = 0.0
        for member in members:
            weight = max(member.strength, 0.05)
            total += weight
            if member.predicted_direction == "positive":
                weighted += weight
            elif member.predicted_direction == "negative":
                weighted -= weight

        direction = "positive" if weighted > 0 else "negative" if weighted < 0 else "neutral"
        first = members[0]
        clustered.append(
            Outcome(
                subject_id=f"{ticker}:{day}",
                ticker=ticker,
                occurred_at=first.occurred_at,
                predicted_direction=direction,
                strength=round(abs(weighted) / total, 4) if total else 0.0,
                forward_return_pct=first.forward_return_pct,
                benchmark_return_pct=first.benchmark_return_pct,
                abnormal_return_pct=first.abnormal_return_pct,
                sessions=first.sessions,
            )
        )
    return clustered


def summarise(subject: str, horizon: int, outcomes: list[Outcome], skipped: int) -> EvaluationReport:
    """Fold outcomes into the report. Pure: no database, no prices."""
    scored = [o for o in outcomes if o.abnormal_return_pct is not None]
    directional = [o for o in scored if o.is_directional]

    positives = [o.abnormal_return_pct for o in directional if o.predicted_direction == "positive"]
    negatives = [o.abnormal_return_pct for o in directional if o.predicted_direction == "negative"]
    neutrals = [o.abnormal_return_pct for o in scored if o.predicted_direction == "neutral"]

    hits = [o.correct for o in directional if o.correct is not None]
    hit_rate = round(sum(1 for h in hits if h) / len(hits), 4) if hits else None

    # The score a coin with a thumb on the scale would get: always call
    # whichever direction the period happened to deliver.
    baseline = None
    if scored:
        down = sum(1 for o in scored if o.abnormal_return_pct < 0) / len(scored)
        baseline = round(max(down, 1 - down), 4)

    mean_pos, mean_neg = _mean(positives), _mean(negatives)
    spread = round(mean_pos - mean_neg, 4) if mean_pos is not None and mean_neg is not None else None

    ic = spearman(
        [o.signed_strength for o in scored],
        [o.abnormal_return_pct for o in scored],
    )

    by_ticker: dict = {}
    for outcome in directional:
        bucket = by_ticker.setdefault(outcome.ticker, {"n": 0, "hits": 0})
        bucket["n"] += 1
        if outcome.correct:
            bucket["hits"] += 1

    return EvaluationReport(
        subject=subject,
        horizon_sessions=horizon,
        evaluated=len(scored),
        skipped_no_prices=skipped,
        directional=len(directional),
        hit_rate=hit_rate,
        baseline_hit_rate=baseline,
        mean_abnormal_positive=mean_pos,
        mean_abnormal_negative=mean_neg,
        mean_abnormal_neutral=_mean(neutrals),
        spread=spread,
        t_statistic=_welch_t(positives, negatives),
        information_coefficient=ic,
        by_ticker=by_ticker,
    )


class _PriceCache:
    """One fetch per ticker for a whole evaluation run."""

    def __init__(self, range_key: str = "1Y"):
        self.range_key = range_key
        self._series: dict[str, Optional[PriceSeries]] = {}

    def get(self, ticker: str) -> Optional[PriceSeries]:
        if ticker not in self._series:
            try:
                self._series[ticker] = get_price_source().get(ticker, self.range_key)
            except Exception:
                logger.warning("Prices unavailable for %s", ticker, exc_info=True)
                self._series[ticker] = None
        return self._series[ticker]


def _score_outcomes(raw: list[Outcome], horizon: int, cache: _PriceCache) -> tuple[list[Outcome], int]:
    benchmark = cache.get(BENCHMARK_TICKER)
    skipped = 0

    for outcome in raw:
        stock = forward_return(cache.get(outcome.ticker), outcome.occurred_at, horizon)
        if stock is None:
            skipped += 1
            continue
        bench = forward_return(benchmark, outcome.occurred_at, horizon)
        if bench is None:
            skipped += 1
            continue

        outcome.forward_return_pct = stock[0]
        outcome.benchmark_return_pct = bench[0]
        outcome.abnormal_return_pct = round(stock[0] - bench[0], 4)
        outcome.sessions = horizon

    return raw, skipped


def evaluate_signals(db: Session, horizon: int = 1, limit: int = 2000) -> EvaluationReport:
    """Did the engine's extracted findings predict anything?

    This is the question that can be answered today. A finding carries the
    document's publication time, so measuring forward from it asks whether the
    information was tradeable when it became public, with no knowledge of the
    future involved.
    """
    company_repo = CompanyRepository(db)
    tickers = {c.id: c.ticker for c in company_repo.list_all()}

    signals = SignalRepository(db).list_feed(limit=limit)
    raw = [
        Outcome(
            subject_id=str(s.id),
            ticker=tickers.get(s.company_id, "?"),
            occurred_at=s.occurred_at,
            predicted_direction=s.market_direction or "neutral",
            strength=s.priority or 0.0,
        )
        for s in signals
        if s.company_id in tickers
    ]

    scored, skipped = _score_outcomes(raw, horizon, _PriceCache())
    return summarise("signals", horizon, cluster_by_event(scored), skipped)


def evaluate_assessments(db: Session, horizon: int = 1, limit: int = 2000) -> EvaluationReport:
    """Did the live fast path predict anything?

    Only meaningful for assessments produced in real time. Replaying historical
    filings through today's priors would score the engine against watch lists
    built from those same filings, which is circular and would look excellent.
    """
    company_repo = CompanyRepository(db)
    tickers = {c.id: c.ticker for c in company_repo.list_all()}

    raw = [
        Outcome(
            subject_id=str(a.id),
            ticker=tickers.get(a.company_id, "?"),
            occurred_at=a.occurred_at,
            predicted_direction=a.direction,
            strength=a.score,
        )
        for a in AssessmentRepository(db).recent(limit=limit)
        if a.company_id in tickers
    ]

    scored, skipped = _score_outcomes(raw, horizon, _PriceCache())
    return summarise("assessments", horizon, cluster_by_event(scored), skipped)


def multiple_testing_threshold(trials: int, alpha: float = 0.05) -> float:
    """The |t| a result must clear when `trials` variants were tried.

    A Bonferroni correction, chosen because it is the conservative one and the
    failure being guarded against is over-claiming. The arithmetic is blunt:
    testing six horizons and keeping the best is six chances to be lucky, so
    the bar for calling any of them real has to rise accordingly.

    Implemented through a normal-tail approximation rather than a t
    distribution, which is close enough at the sample sizes here and avoids a
    dependency for a number that is a guardrail rather than a result.
    """
    if trials < 1:
        raise ValueError("There must be at least one trial.")
    per_test = alpha / trials

    # Inverse normal CDF (Acklam's rational approximation), two-tailed.
    target = 1.0 - per_test / 2.0
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    p_low, p_high = 0.02425, 1 - 0.02425

    if target < p_low:
        q = math.sqrt(-2 * math.log(target))
        z = (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    elif target <= p_high:
        q = target - 0.5
        r = q * q
        z = (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    else:
        q = math.sqrt(-2 * math.log(1 - target))
        z = -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)

    return round(abs(z), 3)


def survives_multiple_testing(reports: Sequence[EvaluationReport], alpha: float = 0.05) -> list[str]:
    """Which horizons, if any, clear the corrected bar. Usually none, correctly."""
    threshold = multiple_testing_threshold(len(reports), alpha)
    return [
        f"{r.subject}@{r.horizon_sessions}"
        for r in reports
        if r.t_statistic is not None and abs(r.t_statistic) >= threshold
    ]
