"""Grouping companies so a factor is compared against the right peers.

Ranking a bank against a software company on return on assets is not a hard
comparison, it is a meaningless one. A bank runs an enormous balance sheet by
design and earns about one percent on it; a software company runs almost none.
Put them in one distribution and every bank lands in the bottom decile on
profitability and leverage, every time, forever, and the result looks like a
finding.

That failure showed up in this project's first universe-wide run: the eight
weakest companies in the whole database were eight banks, in order. Nothing
about their filings had been read. The library had simply measured them with a
ruler built for operating companies.

So sectors come from the SIC code every filer already reports to the SEC,
grouped coarsely. Coarse on purpose: a group needs enough members to rank
within, and a hundred and thirty companies split into eleven GICS sectors
leaves several groups too small to produce a percentile anyone should trust.
"""

from typing import Optional

# SIC major-group ranges to Loom's coarse sectors. The ranges are the SEC's
# own division boundaries, collapsed where two divisions behave alike for the
# purposes of financial-statement analysis.
_SIC_RANGES: tuple[tuple[int, int, str], ...] = (
    (100, 999, "Agriculture"),
    (1000, 1499, "Energy and Materials"),
    (1500, 1799, "Industrials"),
    (2000, 2199, "Consumer Staples"),
    (2200, 2399, "Consumer Discretionary"),
    (2400, 2599, "Industrials"),
    (2600, 2699, "Energy and Materials"),
    (2700, 2799, "Communication Services"),
    (2800, 2899, "Health Care"),
    (2900, 2999, "Energy and Materials"),
    (3000, 3299, "Industrials"),
    (3300, 3499, "Energy and Materials"),
    (3500, 3579, "Technology"),
    (3580, 3599, "Industrials"),
    (3600, 3699, "Technology"),
    (3700, 3799, "Industrials"),
    (3800, 3851, "Health Care"),
    (3852, 3999, "Industrials"),
    (4000, 4499, "Industrials"),
    (4500, 4599, "Industrials"),
    (4600, 4799, "Energy and Materials"),
    (4800, 4899, "Communication Services"),
    (4900, 4999, "Utilities"),
    (5000, 5199, "Industrials"),
    (5200, 5999, "Consumer Discretionary"),
    (6000, 6799, "Financials"),
    (7000, 7299, "Consumer Discretionary"),
    (7300, 7399, "Technology"),
    (7400, 7999, "Consumer Discretionary"),
    (8000, 8099, "Health Care"),
    (8100, 8999, "Industrials"),
)

# Sectors whose financial statements are structurally unlike an operating
# company's. Loom still scores them, and still ranks them against each other,
# but never against the rest of the universe: for a bank, borrowed money is
# inventory, and leverage is the business rather than a warning about it.
STRUCTURALLY_DIFFERENT = frozenset({"Financials", "Utilities"})

# Below this many members a sector cannot host its own ranking and falls back
# to the whole universe. The exception is the structurally different sectors,
# which are dropped from a factor entirely rather than ranked against companies
# they cannot be compared with.
MIN_SECTOR_MEMBERS = 8


def sector_for_sic(sic: Optional[str | int]) -> Optional[str]:
    """Loom's coarse sector for a SEC SIC code."""
    if sic is None:
        return None
    try:
        code = int(str(sic).strip())
    except (TypeError, ValueError):
        return None
    for low, high, name in _SIC_RANGES:
        if low <= code <= high:
            return name
    return None


def comparable_group(
    sector: Optional[str], counts: dict[str, int]
) -> Optional[str]:
    """Which pool a company should be ranked inside.

    Returns the sector when it is populous enough to rank within, "universe"
    when the company can safely be compared against everyone, and None when it
    can be compared against nobody, which is the honest answer for a bank in a
    universe of six banks.
    """
    if sector is None:
        # An unknown sector is compared against the universe. It is the weaker
        # comparison, and it is better than dropping a company because the SEC
        # directory did not resolve.
        return "universe"
    if sector in STRUCTURALLY_DIFFERENT:
        return sector if counts.get(sector, 0) >= MIN_SECTOR_MEMBERS else None
    if counts.get(sector, 0) >= MIN_SECTOR_MEMBERS:
        return sector
    return "universe"


__all__ = [
    "MIN_SECTOR_MEMBERS",
    "STRUCTURALLY_DIFFERENT",
    "comparable_group",
    "sector_for_sic",
]
