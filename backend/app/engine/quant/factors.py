"""The factor library: what Loom measures about a company's reported numbers.

Every factor here is published, replicated, and computable from figures a
filer is required to disclose. That is the selection rule, and it rules things
out: nothing here uses price, nothing uses analyst estimates, and nothing was
invented for this project. Loom has no pricing model, so a factor that needs
one would be a guess wearing a number's clothes.

**Why these and not a hundred others.** Every factor added to a composite that
is not independent of the others adds noise and borrows confidence it did not
earn, and the more factors a screen reports the more convincing it looks
regardless of whether it works. The ten below were chosen to cover distinct
economic claims (earnings quality, growth discipline, dilution, profitability,
solvency) from inputs this database actually holds for most of the universe.

Every factor states its direction explicitly. Several of the best documented
ones are *inverted*: companies that grow assets fastest and issue the most
stock have historically gone on to underperform, which is the opposite of what
the raw number looks like it is saying. A library that left direction implicit
would invite exactly that misreading.

Each returns the inputs it used alongside the value, so any score can be taken
apart back to the filed figures that produced it.
"""

from dataclasses import dataclass, field
from typing import Callable, Optional

from app.engine.quant.series import INSTANT, YEAR, FactSeries

# Below this, a denominator is treated as unusable rather than divided by. A
# company with near-zero assets or near-zero revenue produces ratios in the
# thousands that would dominate every cross-sectional rank they appear in.
MIN_DENOMINATOR = 1.0

# How old the period a factor describes may be, measured from the company's own
# most recent filing. Filers change which XBRL concept they tag a line under,
# and when they do the old concept simply stops updating: the arithmetic still
# works, and quietly describes a fiscal year that ended years ago. Every factor
# checks this, because a stale reading is not a weak signal, it is a wrong one
# presented with the same confidence as a current one.
MAX_PERIOD_AGE_DAYS = 500


@dataclass(frozen=True)
class FactorValue:
    key: str
    value: float
    # The filed figures behind the number, so a conclusion can be audited
    # without re-running the engine.
    inputs: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class Factor:
    key: str
    label: str
    # What a HIGH raw value means for the company. Stated rather than assumed
    # because several of these are inverted relative to intuition.
    higher_is_better: bool
    # One sentence a non-professional can read, explaining what is measured and
    # why anyone cares. Rendered in the interface verbatim.
    meaning: str
    # Where the claim comes from. Not decoration: a factor with no source is a
    # hunch, and this column is what stops the library filling up with them.
    source: str
    compute: Callable[[FactSeries], Optional[FactorValue]]


def _safe_ratio(numerator: Optional[float], denominator: Optional[float]) -> Optional[float]:
    if numerator is None or denominator is None:
        return None
    if abs(denominator) < MIN_DENOMINATOR:
        return None
    return numerator / denominator


def _annual(
    series: FactSeries, metrics: tuple[str, ...], *, back: int = 0
) -> Optional[tuple]:
    """One fiscal year's figures for several metrics at once, or None.

    Wraps `FactSeries.aligned` with the staleness check every factor needs, so
    no factor can forget it.
    """
    found = series.aligned(metrics, YEAR, back=back)
    if found is None:
        return None
    period_end, values = found
    if series.is_stale(period_end, limit_days=MAX_PERIOD_AGE_DAYS):
        return None
    return period_end, values


def _assets_at(series: FactSeries, period_end) -> Optional[float]:
    """Total assets as at a fiscal period end.

    Anchored to the period rather than taken as "the latest": balance sheets
    are filed quarterly, so the most recent assets figure is usually from a
    different period than the annual profit it would be dividing.
    """
    observation = series.instant_on("assets", period_end)
    return observation.value if observation else None


def _average_assets(series: FactSeries, period_end) -> Optional[float]:
    """Mean of opening and closing assets for a fiscal year.

    Flow over closing stock is the common shortcut and it biases every ratio
    for a company that grew during the period, which is precisely the
    population several of these factors are trying to distinguish.
    """
    closing = series.instant_on("assets", period_end)
    if closing is None:
        return None
    pair = series.year_apart_instants("assets", anchor=period_end)
    if pair is None:
        return closing.value
    current, prior = pair
    return (current.value + prior.value) / 2


# ---- earnings quality -------------------------------------------------


def _accruals(series: FactSeries) -> Optional[FactorValue]:
    """Sloan's accrual: the part of profit that is not cash.

    Earnings and cash flow diverge for ordinary reasons every quarter. What
    the research found is that the gap predicts: firms whose profits are least
    backed by cash go on to disappoint, because accruals are where judgement
    lives and judgement bends toward the answer management wants.
    """
    found = _annual(series, ("net_income", "operating_cash_flow"))
    if found is None:
        return None
    period_end, values = found
    assets = _average_assets(series, period_end)
    result = _safe_ratio(values["net_income"] - values["operating_cash_flow"], assets)
    if result is None:
        return None
    return FactorValue(
        key="accruals", value=result,
        inputs={**values, "avg_assets": assets, "period_end": period_end.isoformat()},
    )


def _cash_conversion(series: FactSeries) -> Optional[FactorValue]:
    """Operating cash flow per unit of reported profit.

    The same question as accruals asked the other way round, and kept
    deliberately: the ratio is what a reader can sanity-check against the two
    numbers on the face of the statements, while the accrual is scaled by
    assets and harder to feel. Below 1.0 means profit is running ahead of cash.
    """
    found = _annual(series, ("net_income", "operating_cash_flow"))
    if found is None:
        return None
    period_end, values = found
    if values["net_income"] <= 0:
        # Undefined against a loss rather than misleading: a negative
        # denominator flips the sign and would rank a loss-making company as
        # having excellent cash conversion.
        return None
    result = _safe_ratio(values["operating_cash_flow"], values["net_income"])
    if result is None:
        return None
    return FactorValue(
        key="cash_conversion", value=result,
        inputs={**values, "period_end": period_end.isoformat()},
    )


# ---- growth discipline ------------------------------------------------


def _asset_growth(series: FactSeries) -> Optional[FactorValue]:
    """Year-over-year growth in total assets.

    Among the strongest documented predictors in the cross-section, and
    negatively: the firms that expand their balance sheets fastest tend to
    underperform afterwards. Fast asset growth is empire building and
    acquisition accounting as often as it is opportunity.
    """
    pair = series.year_apart_instants("assets")
    if pair is None:
        return None
    current, previous = pair
    if series.is_stale(current.period_end, limit_days=MAX_PERIOD_AGE_DAYS):
        return None
    result = _safe_ratio(current.value - previous.value, abs(previous.value))
    if result is None:
        return None
    return FactorValue(
        key="asset_growth", value=result,
        inputs={
            "assets": current.value, "assets_prior": previous.value,
            "period_end": current.period_end.isoformat(),
            "prior_period_end": previous.period_end.isoformat(),
        },
    )


def _net_share_issuance(series: FactSeries) -> Optional[FactorValue]:
    """Change in diluted share count.

    What actually happened to a shareholder's slice, which no headline number
    reports. A company can grow profit and shrink your claim on it at the same
    time, and the composite treats issuance as a cost because the evidence says
    the market under-reacts to it.
    """
    current = _annual(series, ("shares_diluted",))
    previous = _annual(series, ("shares_diluted",), back=1)
    if current is None or previous is None:
        return None
    period_end, now = current
    _, before = previous
    result = _safe_ratio(
        now["shares_diluted"] - before["shares_diluted"], abs(before["shares_diluted"])
    )
    if result is None:
        return None
    return FactorValue(
        key="net_share_issuance", value=result,
        inputs={
            "shares_diluted": now["shares_diluted"],
            "shares_diluted_prior": before["shares_diluted"],
            "period_end": period_end.isoformat(),
        },
    )


# ---- profitability ----------------------------------------------------


def _gross_profitability(series: FactSeries) -> Optional[FactorValue]:
    """Gross profit over total assets.

    Novy-Marx's point was that gross profit sits above every line management
    has discretion over, so it is the cleanest profitability signal on the
    statements and it survives where net-income-based measures wash out.
    """
    found = _annual(series, ("gross_profit",))
    if found is None:
        return None
    period_end, values = found
    assets = _average_assets(series, period_end)
    result = _safe_ratio(values["gross_profit"], assets)
    if result is None:
        return None
    return FactorValue(
        key="gross_profitability", value=result,
        inputs={**values, "avg_assets": assets, "period_end": period_end.isoformat()},
    )


def _return_on_assets(series: FactSeries) -> Optional[FactorValue]:
    """Profit per unit of assets employed. The plainest measure of whether the
    business earns its keep."""
    found = _annual(series, ("net_income",))
    if found is None:
        return None
    period_end, values = found
    assets = _average_assets(series, period_end)
    result = _safe_ratio(values["net_income"], assets)
    if result is None:
        return None
    return FactorValue(
        key="return_on_assets", value=result,
        inputs={**values, "avg_assets": assets, "period_end": period_end.isoformat()},
    )


def _operating_margin_change(series: FactSeries) -> Optional[FactorValue]:
    """Year-over-year change in operating margin.

    The level says what kind of business it is; the change says which way it is
    going, and only the second is news. Margin turning down while revenue still
    grows is the pattern a revenue headline hides best.
    """
    current = _annual(series, ("revenue", "operating_income"))
    previous = _annual(series, ("revenue", "operating_income"), back=1)
    if current is None or previous is None:
        return None
    period_end, now = current
    _, before = previous
    margin = _safe_ratio(now["operating_income"], now["revenue"])
    margin_prior = _safe_ratio(before["operating_income"], before["revenue"])
    if margin is None or margin_prior is None:
        return None
    return FactorValue(
        key="operating_margin_change", value=margin - margin_prior,
        inputs={
            "margin": margin, "margin_prior": margin_prior,
            "period_end": period_end.isoformat(),
        },
    )


def _revenue_growth(series: FactSeries) -> Optional[FactorValue]:
    """Year-over-year revenue growth. Included for what it is, the headline
    everyone already reads, so the composite is not silently scoring companies
    on quality alone while a reader assumes growth is in there."""
    current = _annual(series, ("revenue",))
    previous = _annual(series, ("revenue",), back=1)
    if current is None or previous is None:
        return None
    period_end, now = current
    _, before = previous
    result = _safe_ratio(now["revenue"] - before["revenue"], abs(before["revenue"]))
    if result is None:
        return None
    return FactorValue(
        key="revenue_growth", value=result,
        inputs={
            "revenue": now["revenue"], "revenue_prior": before["revenue"],
            "period_end": period_end.isoformat(),
        },
    )


def _asset_turnover_change(series: FactSeries) -> Optional[FactorValue]:
    """Change in revenue generated per unit of assets. Piotroski's efficiency
    test: whether the balance sheet is working harder or just getting bigger."""
    current = _annual(series, ("revenue",))
    previous = _annual(series, ("revenue",), back=1)
    if current is None or previous is None:
        return None
    period_end, now = current
    prior_end, before = previous
    assets = _assets_at(series, period_end)
    assets_prior = _assets_at(series, prior_end)
    turnover = _safe_ratio(now["revenue"], assets)
    turnover_prior = _safe_ratio(before["revenue"], assets_prior)
    if turnover is None or turnover_prior is None:
        return None
    return FactorValue(
        key="asset_turnover_change", value=turnover - turnover_prior,
        inputs={
            "turnover": turnover, "turnover_prior": turnover_prior,
            "period_end": period_end.isoformat(),
        },
    )


# ---- solvency ---------------------------------------------------------


def _leverage_change(series: FactSeries) -> Optional[FactorValue]:
    """Change in liabilities as a share of assets.

    Rising leverage is how a company buys growth it could not earn, and it is
    the variable that turns a bad quarter into a solvency question.
    """
    liabilities = series.year_apart_instants("liabilities")
    if liabilities is None:
        return None
    current, previous = liabilities
    if series.is_stale(current.period_end, limit_days=MAX_PERIOD_AGE_DAYS):
        return None
    assets = _assets_at(series, current.period_end)
    assets_prior = _assets_at(series, previous.period_end)
    leverage = _safe_ratio(current.value, assets)
    leverage_prior = _safe_ratio(previous.value, assets_prior)
    if leverage is None or leverage_prior is None:
        return None
    return FactorValue(
        key="leverage_change", value=leverage - leverage_prior,
        inputs={
            "leverage": leverage, "leverage_prior": leverage_prior,
            "period_end": current.period_end.isoformat(),
        },
    )


def _cash_to_assets(series: FactSeries) -> Optional[FactorValue]:
    """Cash as a share of the balance sheet. Not a performance measure: it is
    how much room a company has to be wrong for a year."""
    cash = series.latest("cash", INSTANT)
    if cash is None:
        return None
    if series.is_stale(cash.period_end, limit_days=MAX_PERIOD_AGE_DAYS):
        return None
    assets = _assets_at(series, cash.period_end)
    result = _safe_ratio(cash.value, assets)
    if result is None:
        return None
    return FactorValue(
        key="cash_to_assets", value=result,
        inputs={
            "cash": cash.value, "assets": assets,
            "period_end": cash.period_end.isoformat(),
        },
    )


FACTORS: tuple[Factor, ...] = (
    Factor(
        key="accruals", label="Earnings backed by cash", higher_is_better=False,
        meaning="How much of the reported profit did not arrive as cash. A high "
                "reading means the profit rests on judgement calls rather than money received.",
        source="Sloan (1996), the accrual anomaly",
        compute=_accruals,
    ),
    Factor(
        key="cash_conversion", label="Cash per dollar of profit", higher_is_better=True,
        meaning="Cash generated for every dollar of reported profit. Below one dollar "
                "means the profit is running ahead of the cash.",
        source="Standard earnings-quality diagnostic",
        compute=_cash_conversion,
    ),
    Factor(
        key="asset_growth", label="Balance sheet expansion", higher_is_better=False,
        meaning="How fast the company grew its total assets. Companies that expand "
                "fastest have historically gone on to disappoint.",
        source="Cooper, Gulen and Schill (2008), the asset growth effect",
        compute=_asset_growth,
    ),
    Factor(
        key="net_share_issuance", label="Dilution of your stake", higher_is_better=False,
        meaning="Whether the company issued more shares. New shares shrink the slice "
                "each existing share owns, even when profit is rising.",
        source="Daniel and Titman (2006); Pontiff and Woodgate (2008)",
        compute=_net_share_issuance,
    ),
    Factor(
        key="gross_profitability", label="Gross profitability", higher_is_better=True,
        meaning="Gross profit relative to the assets used to produce it, measured "
                "above the lines management has most discretion over.",
        source="Novy-Marx (2013), the quality factor",
        compute=_gross_profitability,
    ),
    Factor(
        key="return_on_assets", label="Return on assets", higher_is_better=True,
        meaning="Profit earned per dollar of assets. The plainest test of whether "
                "the business earns its keep.",
        source="Piotroski (2000), F-score component",
        compute=_return_on_assets,
    ),
    Factor(
        key="operating_margin_change", label="Margin direction", higher_is_better=True,
        meaning="Whether the operating margin widened or narrowed against last year. "
                "Margins turning down while revenue grows is easy to miss.",
        source="Piotroski (2000), F-score component",
        compute=_operating_margin_change,
    ),
    Factor(
        key="revenue_growth", label="Revenue growth", higher_is_better=True,
        meaning="How fast sales grew against last year.",
        source="Reported for completeness beside the quality measures",
        compute=_revenue_growth,
    ),
    Factor(
        key="asset_turnover_change", label="Asset efficiency", higher_is_better=True,
        meaning="Whether each dollar of assets is producing more revenue than last "
                "year, or whether the balance sheet is simply getting bigger.",
        source="Piotroski (2000), F-score component",
        compute=_asset_turnover_change,
    ),
    Factor(
        key="leverage_change", label="Debt direction", higher_is_better=False,
        meaning="Whether liabilities grew as a share of the balance sheet. Rising "
                "leverage is what turns a bad year into a solvency question.",
        source="Piotroski (2000), F-score component",
        compute=_leverage_change,
    ),
    Factor(
        key="cash_to_assets", label="Cash cushion", higher_is_better=True,
        meaning="How much of the balance sheet is cash. Not a measure of performance, "
                "a measure of how long the company can afford to be wrong.",
        source="Standard liquidity diagnostic",
        compute=_cash_to_assets,
    ),
)

FACTORS_BY_KEY: dict[str, Factor] = {factor.key: factor for factor in FACTORS}


def compute_all(series: FactSeries) -> dict[str, FactorValue]:
    """Every factor that can be computed from what this company has filed.

    A factor whose inputs are missing is absent from the result rather than
    zero. Zero is a reading; missing is the absence of one, and a composite
    that cannot tell them apart scores a company on data it never had.
    """
    out: dict[str, FactorValue] = {}
    for factor in FACTORS:
        try:
            value = factor.compute(series)
        except Exception:
            # One malformed filing must not take down a universe-wide run.
            continue
        if value is not None:
            out[factor.key] = value
    return out


__all__ = ["FACTORS", "FACTORS_BY_KEY", "Factor", "FactorValue", "compute_all"]
