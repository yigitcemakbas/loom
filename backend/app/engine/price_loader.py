"""Loading a company's stored price history, and the benchmark beside it.

The boundary engine/price_context.py does not cross. That module is pure
arithmetic over sessions and holds no session, which is what lets the case
file's ordering be tested without a database; this one does the reading.

The benchmark is loaded once and held. Every company is measured against the
same series, it is twenty years of daily closes, and fetching it again for each
company on a page would be the single largest query on the request.
"""

import logging
import threading
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select

from app.engine.quant.prices import PriceHistory, history_from_bars
from app.models.company import Company
from app.models.price_bar import PriceBar

logger = logging.getLogger(__name__)

# The series every company's move is measured against. The same one the
# evaluation harness uses, deliberately: two different benchmarks would mean
# the reaction shown on a company page and the reaction scored in a backtest
# were different numbers wearing one name.
BENCHMARK_TICKER = "QQQ"

# How much history a reaction needs behind it. Enough for the volatility
# baseline plus the window being measured, with room for holidays.
LOOKBACK_DAYS = 420

_lock = threading.Lock()
_benchmark: tuple[datetime, PriceHistory | None] | None = None
_BENCHMARK_TTL_SECONDS = 900


def load_history(db, company_id, *, since: date | None = None) -> PriceHistory | None:
    """One company's stored closes, or None when Loom holds none.

    None rather than an empty history, so a caller cannot accidentally treat a
    company with no prices as one whose price did not move.
    """
    since = since or (date.today() - timedelta(days=LOOKBACK_DAYS))
    rows = list(db.execute(
        select(PriceBar)
        .where(PriceBar.company_id == company_id)
        .where(PriceBar.session_date >= since)
        .order_by(PriceBar.session_date)
    ).scalars())
    return history_from_bars(rows) if rows else None


def load_benchmark(db) -> PriceHistory | None:
    """The market series, held between requests.

    Never raises into a caller: a missing benchmark costs the market adjustment
    and nothing else, and an unadjusted move is a worse answer than an adjusted
    one but a much better answer than a failed page.
    """
    global _benchmark
    now = datetime.now(timezone.utc)
    with _lock:
        if _benchmark is not None and (now - _benchmark[0]).total_seconds() < _BENCHMARK_TTL_SECONDS:
            return _benchmark[1]

    history = None
    try:
        company = db.execute(
            select(Company).where(Company.ticker == BENCHMARK_TICKER)
        ).scalars().first()
        if company is not None:
            history = load_history(db, company.id)
        else:
            logger.info("No %s series stored; price moves will not be market adjusted", BENCHMARK_TICKER)
    except Exception:
        logger.exception("Benchmark unavailable; price moves will not be market adjusted")

    with _lock:
        _benchmark = (now, history)
    return history


# How many peers are loaded for a sector comparison. Enough to describe a
# group, bounded because a sector page should not read forty companies' price
# histories to say one sentence about the group.
MAX_PEERS = 25


def load_peers(db, company, *, limit: int = MAX_PEERS) -> list[PriceHistory]:
    """The price histories of a company's sector, excluding the company itself.

    Excluding it is the point rather than a detail. A "sector" that includes
    the filer is partly the filer, and the question being asked is whether the
    disclosure reached companies that did not make it.
    """
    if company.sector is None:
        return []
    # Ordered, and the ordering is the point. Without it this was
    # `limit(25)` over whatever rows the database returned first, which at a
    # hundred and thirty companies was most of the sector and did not show. At
    # a thousand, Technology has two hundred and thirty members and an
    # unordered twenty-five of them is a different peer group on every query,
    # so the same filing would be measured against a different industry each
    # time the page loaded.
    #
    # Largest first. A sector's biggest names are the ones that move it, and an
    # equal-weighted index of its smallest twenty-five would describe the
    # micro-cap tail rather than the industry. Companies with no rank sort
    # last rather than being dropped.
    peers = db.execute(
        select(Company)
        .where(Company.sector == company.sector)
        .where(Company.id != company.id)
        .order_by(Company.sec_rank.asc().nullslast(), Company.ticker.asc())
        .limit(limit)
    ).scalars()
    found = []
    for peer in peers:
        history = load_history(db, peer.id)
        if history is not None:
            found.append(history)
    return found


def reset_cache() -> None:
    """Drop the held benchmark. For tests, and after a price ingest run."""
    global _benchmark
    with _lock:
        _benchmark = None


__all__ = [
    "BENCHMARK_TICKER",
    "LOOKBACK_DAYS",
    "MAX_PEERS",
    "load_benchmark",
    "load_history",
    "load_peers",
    "reset_cache",
]
