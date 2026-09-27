"""Loading the measured tables from the corpus, and holding them.

engine/disclosure.py is pure on purpose: hand it findings, get an expectation
table, no session anywhere. That is what lets the synthesis be tested without a
database. This module is the boundary it does not cross.

**Why it is cached.** The table is a property of Loom's whole corpus rather
than of any one company, so every brief needs the same one, and rebuilding it
per request would mean reading every stored finding to answer a question about
a single company. It changes only when the reading engine produces new
findings, which happens on a scheduled job rather than on a page view, so a
short time-to-live is the right shape: a request is never more than a minute
behind the corpus, and a burst of requests reads it once.

The cache is deliberately not invalidated by writes. A brief built against a
table sixty seconds stale is correct in every way that matters, and coupling
the extraction pipeline to a cache in the synthesis layer would be a real
dependency bought for an imaginary problem.
"""

import logging
import threading
from datetime import date, datetime, timedelta, timezone

from app.engine.disclosure import DisclosureNorms, measure_norms
from app.engine.precedent import Case, PrecedentBase, measure_precedents, topics_of_signal
from app.repositories.signal_repository import SignalRepository

logger = logging.getLogger(__name__)

# How far back the expectation is measured. Deliberately the whole corpus
# rather than a trailing year: this table describes how documents of a kind are
# written, which is a property of the genre and of accounting convention, not
# of the current cycle. Restricting it to recent filings would rebuild the
# small sample problem it exists to solve.
LOOKBACK_YEARS = 25

# How long a loaded table is reused. See the module docstring.
TTL_SECONDS = 60

_lock = threading.Lock()
_cached: tuple[datetime, DisclosureNorms] | None = None


def load_norms(db, *, force: bool = False) -> DisclosureNorms:
    """The measured expectation table, reused within its time-to-live.

    Never raises into a caller. A brief built without norms is the older,
    worse answer rather than no answer, and the one screen that must keep
    working when something else is broken is this one.
    """
    global _cached
    now = datetime.now(timezone.utc)
    with _lock:
        if not force and _cached is not None and (now - _cached[0]).total_seconds() < TTL_SECONDS:
            return _cached[1]

    try:
        from sqlalchemy import select

        from app.models.company import Company

        signals = SignalRepository(db).list_for_global_prior(
            since=now - timedelta(days=365 * LOOKBACK_YEARS),
            limit=100_000,
        )
        # Which industry each company is in. Loaded here rather than inside the
        # measurement, which holds no session by design; without it the table
        # loses its sector rung and every company is scored against a corpus
        # that is half technology.
        sectors = {
            str(cid): sector
            for cid, sector in db.execute(
                select(Company.id, Company.sector).where(Company.sector.isnot(None))
            ).all()
        }
        norms = measure_norms(signals, sectors)
    except Exception:
        logger.exception("disclosure norms could not be measured; briefs fall back to raw direction")
        return DisclosureNorms()

    with _lock:
        _cached = (now, norms)
    return norms


# The precedent base is rebuilt far less often than the norms table and costs
# far more to build: it needs every company's price history, not just the
# findings. An hour is right for something that changes only when a scheduled
# job adds findings or prices.
PRECEDENT_TTL_SECONDS = 3600

# How much price history a case needs behind its earliest filing: roughly six
# months for the volatility baseline, plus room for holidays and a thin series.
CASE_LOOKBACK_DAYS = 300

_precedents: tuple[datetime, PrecedentBase] | None = None


def load_precedents(db, *, force: bool = False) -> PrecedentBase:
    """What has historically followed each kind of disclosure, held between requests.

    Expensive enough to be worth the cache and cheap enough not to need a
    stored table: one pass over the findings and one price history per company.
    Building it per request would mean reading every company's twenty years of
    closes to answer a question about one company's page.

    One case per filing, not one per finding. Forty findings from one annual
    report all measure the same fortnight, and counting them separately turns
    one company's one bad quarter into overwhelming evidence for whatever those
    forty sentences happened to be about.

    Never raises into a caller. A page without precedents is the page as it was
    last week; a page that five hundred everseven hundred database rows could
    break is not worth the feature.
    """
    global _precedents
    now = datetime.now(timezone.utc)
    with _lock:
        if (
            not force and _precedents is not None
            and (now - _precedents[0]).total_seconds() < PRECEDENT_TTL_SECONDS
        ):
            return _precedents[1]

    try:
        base = measure_precedents(_collect_cases(db))
    except Exception:
        logger.exception("Precedents could not be measured; findings show without them")
        return PrecedentBase()

    with _lock:
        _precedents = (now, base)
    return base


def _collect_cases(db) -> list[Case]:
    """Every (filing, topic) pair Loom holds, with what the market did after it."""
    from sqlalchemy import select

    from app.engine.price_context import moves_for
    from app.engine.price_loader import load_benchmark, load_history
    from app.models.company import Company
    from app.models.signal import Signal

    benchmark = load_benchmark(db)
    companies = {c.id: c for c in db.execute(select(Company)).scalars()}

    by_company: dict[object, list] = {}
    for signal in db.execute(select(Signal)).scalars():
        by_company.setdefault(signal.company_id, []).append(signal)

    # The earliest filing any case could be measured from. Bounds how far back
    # price history has to be read.
    oldest = min(
        (s.occurred_at.date() for signals in by_company.values() for s in signals),
        default=date.today(),
    )

    cases: list[Case] = []
    for company_id, signals in by_company.items():
        company = companies.get(company_id)
        if company is None:
            continue
        # Two years, not twenty. A reaction needs the fortnight after the
        # filing plus roughly six months before it for the volatility baseline,
        # and the filings span about a year, so anything older is read from the
        # database and discarded. On the stored corpus that was most of the
        # cost of building this table.
        history = load_history(db, company_id, since=oldest - timedelta(days=CASE_LOOKBACK_DAYS))
        if history is None:
            continue
        moves = moves_for(signals, history, benchmark)

        # Collapsed here rather than at the end: the key is the filing and the
        # topic together, so one annual report contributes one case per topic
        # it discusses and not one per sentence.
        seen: set[tuple] = set()
        for signal in signals:
            move = moves.get(str(signal.id))
            if move is None:
                continue
            for topic in topics_of_signal(signal):
                key = (company.ticker, move.as_of, topic)
                if key in seen:
                    continue
                seen.add(key)
                cases.append(Case(
                    topic=topic,
                    sector=company.sector,
                    ticker=company.ticker,
                    move_percent=move.abnormal_percent,
                ))
    return cases


def warm(db) -> None:
    """Build both tables now, so no page view has to.

    Called at startup and on a timer. The precedent base reads a price history
    per read company and computes a volatility baseline per filing date; at a
    thousand companies that measured 1.5 seconds, which a reader was paying
    once an hour whenever their page view happened to be the one that found the
    cache expired. Derived data that takes over a second to build belongs on a
    schedule, not on the hot path.

    Never raises. A failed warm costs the next reader the old latency, which is
    a worse page rather than no page.
    """
    try:
        load_norms(db, force=True)
        load_precedents(db, force=True)
    except Exception:
        logger.exception("Could not warm the measured tables; they build on demand instead")


def reset_cache() -> None:
    """Drop the held tables. For tests, and for a job that has just written a
    large batch of findings and wants its own brief pass to see them."""
    global _cached, _precedents
    with _lock:
        _cached = None
        _precedents = None


__all__ = [
    "LOOKBACK_YEARS",
    "PRECEDENT_TTL_SECONDS",
    "TTL_SECONDS",
    "load_norms",
    "load_precedents",
    "warm",
    "reset_cache",
]
