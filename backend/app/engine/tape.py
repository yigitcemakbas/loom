"""The running feed across the top of the dashboard.

Two kinds of thing belong on a tape, and they answer different questions. An
upcoming earnings date answers "what is about to happen"; a news headline
answers "what just did". Interleaving them is the point: a report three days
out reads differently when the last four headlines about that company were
downgrades.

Everything here is arithmetic and string assembly over data already stored.
No model is called, which matters because this is the first thing on screen
and the most frequently refreshed thing in the product.

**On saying how a report will "affect the market".** Nothing in this pipeline
prices a stock or forecasts a move, and inventing an expected move would be
fabricated authority of exactly the kind `earnings.py` refuses. What can be
said honestly is what the market has already done and how it is positioned
going in:

  - the realised move after the *last* report, labelled as the single
    observation it is rather than dressed up as a typical move,
  - how crowded the short side is, which is the clearest read on who is
    exposed if the print surprises,
  - the beat/miss record, but only once there are enough reports for it to be
    something other than anecdote.

Each of those is a fact with a date on it. None of them is a prediction.
"""

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.engine.earnings import MIN_HISTORY_FOR_RECORD, build_outlook
from app.engine.market_context import reaction_since
from app.ingestion.prices import get_price_source
from app.models.structured_fact import FactType
from app.repositories.company_repository import CompanyRepository
from app.repositories.document_repository import DocumentRepository
from app.repositories.fact_repository import FactRepository
from app.repositories.watchlist_repository import WatchlistRepository

logger = logging.getLogger(__name__)

# Beyond this an earnings date is context rather than a call to action.
EARNINGS_WITHIN_DAYS = 21

# An earnings move happens on the next open. Ten sessions would blend it into
# a fortnight of unrelated drift; two covers the print and the day after.
EARNINGS_REACTION_SESSIONS = 2

# Headlines older than this are not news.
NEWS_WITHIN_DAYS = 5
MAX_NEWS_ITEMS = 24

# Days-to-cover above which the short side is crowded enough to be worth
# naming going into a print. Matches the frontend's short interest panel.
CROWDED_DAYS_TO_COVER = 5.0

# A single stock cannot fill the tape with its own news.
MAX_NEWS_PER_TICKER = 3


@dataclass
class TapeItem:
    kind: str                       # "earnings" | "news"
    ticker: str
    label: str                      # the leading chip: "3D", "TODAY", "NEWS"
    headline: str
    detail: str | None = None
    tone: str = "neutral"           # "urgent" | "positive" | "negative" | "neutral"
    occurred_at: datetime | None = None
    href: str | None = None
    sources: list[str] = field(default_factory=list)


def _money(value) -> str | None:
    if value in (None, ""):
        return None
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if abs(v) >= 1e9:
        return f"${v / 1e9:.1f}B"
    if abs(v) >= 1e6:
        return f"${v / 1e6:.0f}M"
    return f"${v:,.0f}"


def _countdown(days_until: int) -> str:
    if days_until == 0:
        return "TODAY"
    if days_until == 1:
        return "TOMORROW"
    return f"{days_until}D"


def _last_report_move(ticker: str, events: list) -> str | None:
    """What the stock actually did after its most recent report.

    Deliberately the last one and not an average. With one or two reported
    quarters stored, an average is a single number wearing a statistic's
    clothes, and a reader would reasonably take it as "this is what usually
    happens" when it means "this is what happened once".
    """
    reported = [e for e in events if (e.attributes or {}).get("reported")]
    if not reported:
        return None

    latest = max(reported, key=lambda e: e.as_of_date)
    when = datetime.combine(latest.as_of_date, datetime.min.time(), tzinfo=timezone.utc)

    try:
        series = get_price_source().get(ticker, "1Y")
    except Exception:
        logger.warning("Tape: price series unavailable for %s", ticker, exc_info=True)
        return None

    reaction = reaction_since(series, when, sessions=EARNINGS_REACTION_SESSIONS)
    if reaction is None:
        return None

    direction = "fell" if reaction.change_percent < 0 else "rose"
    surprise = (latest.attributes or {}).get("eps_surprise_percent")
    surprise_note = ""
    if surprise is not None:
        try:
            surprise_note = f" on a {float(surprise):+.1f}% EPS surprise"
        except (TypeError, ValueError):
            surprise_note = ""

    return (
        f"Last report {latest.as_of_date:%d %b}: the stock {direction} "
        f"{abs(reaction.change_percent):.1f}% over the next "
        f"{reaction.sessions} session{'s' if reaction.sessions != 1 else ''}{surprise_note}"
    )


def _short_positioning(fact_repo: FactRepository, company_id) -> str | None:
    """How exposed the short side is going into the print."""
    readings = fact_repo.list_for_company(
        company_id, fact_type=FactType.SHORT_INTEREST, limit=1
    )
    if not readings:
        return None

    dtc = (readings[0].attributes or {}).get("days_to_cover")
    if dtc is None:
        return None
    try:
        dtc = float(dtc)
    except (TypeError, ValueError):
        return None

    if dtc >= CROWDED_DAYS_TO_COVER:
        return (
            f"{dtc:.1f} days to cover, a crowded short going in: an upside "
            f"surprise forces buying"
        )
    return f"{dtc:.1f} days to cover, so the short side is not crowded"


def _earnings_items(db: Session, today: date) -> list[TapeItem]:
    watchlist_repo = WatchlistRepository(db)
    fact_repo = FactRepository(db)
    watchlist = watchlist_repo.get_or_create_default()

    items: list[TapeItem] = []
    for company in watchlist_repo.list_companies(watchlist.id):
        events = fact_repo.latest_per_date(fact_repo.earnings_events(company.id))
        outlook = build_outlook(events, today=today)

        if outlook.days_until is None or outlook.days_until > EARNINGS_WITHIN_DAYS:
            continue

        parts: list[str] = []
        when = f"{outlook.next_date:%a %d %b}" if outlook.next_date else None
        if when and outlook.when_label:
            parts.append(f"Reports {when}, {outlook.when_label}")
        elif when:
            parts.append(f"Reports {when}")

        eps, revenue = outlook.eps_estimate, _money(outlook.revenue_estimate)
        if eps is not None and revenue:
            parts.append(f"consensus ${float(eps):.2f} EPS on {revenue}")
        elif eps is not None:
            parts.append(f"consensus ${float(eps):.2f} EPS")

        move = _last_report_move(company.ticker, events)
        if move:
            parts.append(move)

        positioning = _short_positioning(fact_repo, company.id)
        if positioning:
            parts.append(positioning)

        if outlook.reports_seen >= MIN_HISTORY_FOR_RECORD:
            parts.append(
                f"ahead of consensus {outlook.beats} of the last {outlook.reports_seen}"
            )

        quarter = f"{outlook.quarter_label} " if outlook.quarter_label else ""
        items.append(
            TapeItem(
                kind="earnings",
                ticker=company.ticker,
                label=_countdown(outlook.days_until),
                headline=f"{quarter}earnings".strip(),
                # Middot rather than full stops: these are clauses read in
                # passing on a moving strip, not sentences.
                detail=" · ".join(parts) if parts else None,
                tone="urgent" if outlook.days_until <= 1 else "accent",
                occurred_at=(
                    datetime.combine(outlook.next_date, datetime.min.time(), tzinfo=timezone.utc)
                    if outlook.next_date
                    else None
                ),
            )
        )

    items.sort(key=lambda i: i.occurred_at or datetime.max.replace(tzinfo=timezone.utc))
    return items


def _news_items(db: Session, now: datetime) -> list[TapeItem]:
    """Recent headlines about tracked companies, newest first.

    Capped per ticker so one company having a noisy week cannot crowd the rest
    of the watchlist off a feed whose whole purpose is breadth.
    """
    document_repo = DocumentRepository(db)
    company_repo = CompanyRepository(db)
    watchlist_repo = WatchlistRepository(db)

    watchlist = watchlist_repo.get_or_create_default()
    tracked = {c.id: c.ticker for c in watchlist_repo.list_companies(watchlist.id)}
    if not tracked:
        return []

    cutoff = now - timedelta(days=NEWS_WITHIN_DAYS)
    # Over-fetch: the query cannot filter to the watchlist or to the cutoff, so
    # the trimming happens here and needs headroom to trim from.
    documents = document_repo.list_all(doc_subtype="news", limit=MAX_NEWS_ITEMS * 8)

    per_ticker: dict[str, int] = {}
    items: list[TapeItem] = []
    for doc in documents:
        ticker = tracked.get(doc.company_id)
        if ticker is None or not doc.title:
            continue
        if doc.published_at is None or doc.published_at < cutoff:
            continue
        if per_ticker.get(ticker, 0) >= MAX_NEWS_PER_TICKER:
            continue

        per_ticker[ticker] = per_ticker.get(ticker, 0) + 1
        items.append(
            TapeItem(
                kind="news",
                ticker=ticker,
                label="NEWS",
                headline=doc.title.strip(),
                occurred_at=doc.published_at,
                href=doc.source_url,
                sources=[doc.source_name] if doc.source_name else [],
            )
        )
        if len(items) >= MAX_NEWS_ITEMS:
            break

    return items


def build_tape(db: Session, *, now: datetime | None = None) -> list[TapeItem]:
    """Earnings first, then news, both newest-relevant first.

    Earnings lead unconditionally. A scheduled event a reader can still act on
    outranks a headline about something already priced, and the tape is read in
    passing rather than studied, so the ordering is the only editing it gets.
    """
    now = now or datetime.now(timezone.utc)
    earnings = _earnings_items(db, now.date())
    news = _news_items(db, now)
    return earnings + news
