"""How much each factor should count, for this company rather than in general.

The composite came out of its first backtest flat: a spread of +0.21% with a
t-statistic of 0.40 over fifty-five rebalances. The diagnosis is not that the
factors are worthless, because several of them carried real signal in opposite
directions. It is that averaging them equally is the wrong operation.

Two things were wrong with the equal-weighted mean.

**It weighted themes by how many factors happened to be written.** There are
four valuation measures and two price-behaviour measures in the library, so
valuation silently received twice the vote of momentum and volatility for no
reason other than authorship. Themes are now averaged internally and then
combined, so adding a fifth value measure no longer makes value more important.

**It counted every factor for every company.** A bank's leverage is not a
warning, it is the product; an asset-light company's book value does not
measure what it owns. Loom ranked all 128 companies on both regardless. The
relevance rules below switch factors off where the economics say the measure
does not apply.

**These weights are not fitted, and must not become fitted.** Every rule here
states a reason that was true before the backtest ran, and none was chosen
because a factor performed badly in 2021-2026. Volatility and gross
profitability both ranked returns backwards over that window; neither is
downweighted here, because a five-year regime is not evidence about a measure
and reweighting on it would be fitting eighteen parameters to fifty-five
observations.
"""

from dataclasses import dataclass
from typing import Optional

from app.engine.quant.factors import FACTORS_BY_KEY

# The economic question each factor answers. Combining within a theme and then
# across themes stops the count of factors in a theme from deciding its weight.
THEMES: dict[str, tuple[str, ...]] = {
    "earnings quality": ("accruals", "cash_conversion"),
    "profitability": ("return_on_assets", "gross_profitability", "operating_margin_change"),
    "growth": ("revenue_growth", "asset_turnover_change"),
    "discipline": ("asset_growth", "net_share_issuance"),
    "solvency": ("leverage_change", "cash_to_assets"),
    "valuation": ("earnings_yield", "cash_flow_yield", "sales_yield", "book_to_price"),
    "price behaviour": ("momentum", "volatility"),
}

THEME_OF: dict[str, str] = {
    key: theme for theme, keys in THEMES.items() for key in keys
}

# Factors that measure nothing for a bank or an insurer. Borrowed money is a
# financial company's raw material, so leverage rising is expansion rather than
# risk, and its balance sheet grows with its business rather than despite it.
# Operating cash flow is routinely negative for reasons that have nothing to do
# with the health of the business, which makes every ratio built on it noise.
_IRRELEVANT_FOR_FINANCIALS = frozenset({
    "leverage_change",
    "asset_growth",
    "cash_conversion",
    "accruals",
    "cash_flow_yield",
    "cash_to_assets",
    "asset_turnover_change",
})

# Utilities are run at deliberately high leverage against a regulated return,
# so the same two measures describe the regulatory regime rather than the
# company's choices.
_IRRELEVANT_FOR_UTILITIES = frozenset({"leverage_change", "asset_growth"})

# Below this share of revenue spent on research, a company is not asset-light
# enough for the book-value critique to bite.
RND_INTENSITY_THRESHOLD = 0.10

# What an inapplicable factor is worth. Zero, not a small number: a measure
# that does not describe this company should not contribute a middling reading
# to its score, because a middling reading is itself a claim.
OFF = 0.0
FULL = 1.0
# Applies where a measure still says something but is known to be distorted.
REDUCED = 0.4


@dataclass(frozen=True)
class Relevance:
    weight: float
    # Stated wherever a factor is switched off or turned down, so a reader can
    # see that Loom decided not to count something rather than not having it.
    reason: Optional[str] = None


def relevance_for(
    key: str, *, sector: Optional[str], rnd_intensity: Optional[float] = None
) -> Relevance:
    """How much this factor should count for a company of this kind."""
    if sector == "Financials" and key in _IRRELEVANT_FOR_FINANCIALS:
        return Relevance(
            OFF,
            "Borrowed money is a financial company's raw material rather than a "
            "risk it took on, so this measure describes the business model, not "
            "how the business is doing.",
        )
    if sector == "Utilities" and key in _IRRELEVANT_FOR_UTILITIES:
        return Relevance(
            OFF,
            "Utilities run at deliberately high leverage against a regulated "
            "return, so this measures the regulatory regime rather than the "
            "company's choices.",
        )
    if (
        key == "book_to_price"
        and rnd_intensity is not None
        and rnd_intensity >= RND_INTENSITY_THRESHOLD
    ):
        return Relevance(
            REDUCED,
            "Book value counts factories and not research, so a company spending "
            "this heavily on research looks expensive on it by construction.",
        )
    return Relevance(FULL)


@dataclass(frozen=True)
class WeightedComposite:
    """A score, and everything needed to argue with it."""

    score: float
    themes: dict[str, float]
    factor_count: int
    # Factors switched off for this company, with the reason each was dropped.
    excluded: dict[str, str]


def weighted_composite(
    percentiles: dict[str, float],
    *,
    sector: Optional[str],
    rnd_intensity: Optional[float] = None,
    min_themes: int = 3,
) -> Optional[WeightedComposite]:
    """Average within each theme, then across the themes that survived.

    Returns None below `min_themes`. A score folded from one theme is not a
    weak summary of a company, it is a measurement of one aspect of it wearing
    the name of a summary, and callers reliably forget the difference.
    """
    by_theme: dict[str, list[float]] = {}
    excluded: dict[str, str] = {}
    counted = 0

    for key, percentile in percentiles.items():
        if key not in FACTORS_BY_KEY:
            continue
        relevance = relevance_for(key, sector=sector, rnd_intensity=rnd_intensity)
        if relevance.weight <= 0:
            excluded[key] = relevance.reason or "Not applicable to this company."
            continue
        theme = THEME_OF.get(key)
        if theme is None:
            continue
        # A reduced weight is applied by pulling the reading toward the middle
        # rather than by dropping it: the measure still says something, it is
        # simply known to be distorted for this kind of company.
        adjusted = 0.5 + (percentile - 0.5) * relevance.weight
        by_theme.setdefault(theme, []).append(adjusted)
        counted += 1

    if len(by_theme) < min_themes:
        return None

    theme_scores = {
        theme: sum(values) / len(values) for theme, values in by_theme.items()
    }
    return WeightedComposite(
        score=round(sum(theme_scores.values()) / len(theme_scores), 4),
        themes={k: round(v, 4) for k, v in theme_scores.items()},
        factor_count=counted,
        excluded=excluded,
    )


__all__ = [
    "OFF",
    "REDUCED",
    "RND_INTENSITY_THRESHOLD",
    "THEMES",
    "THEME_OF",
    "Relevance",
    "WeightedComposite",
    "relevance_for",
    "weighted_composite",
]
