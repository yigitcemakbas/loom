"""What a reader needs in the days around an earnings event.

An investor's decisions cluster around events, and the hours before a company
reports are when accumulated evidence has to be weighed. Until this existed
Loom had no concept of *when*: it could describe what a company disclosed last
quarter but not that the company reports tonight.

Deterministic, like `brief.py` and for the same reasons. Dates, consensus
figures, and a beat/miss record are arithmetic over stored facts, and the
screen that matters most on the busiest day must not depend on a provider
being up.

It deliberately stops short of saying whether to buy or sell. Nothing in this
pipeline prices the stock, knows the reader's position, or models what is
already reflected in the price, so a recommendation would be fabricated
authority. What it can honestly do is put the date, the expectations, the
track record, and the evidence in one place at the moment they are needed.
"""

from dataclasses import dataclass
from datetime import date

# A report inside this window is "imminent": close enough that a reader opening
# the app should be shown it before anything else.
IMMINENT_DAYS = 7

# How many past reports make a meaningful beat/miss record. Fewer than three
# and the pattern is anecdote.
MIN_HISTORY_FOR_RECORD = 3


@dataclass
class EarningsOutlook:
    next_date: date | None
    days_until: int | None
    when_label: str | None          # "after market close", etc.
    eps_estimate: float | None
    revenue_estimate: float | None
    quarter_label: str | None
    is_imminent: bool
    # Track record against consensus, from reported events.
    reports_seen: int
    beats: int
    misses: int
    average_surprise_percent: float | None
    last_surprise_percent: float | None
    headline: str


def _attr(fact, key):
    return (fact.attributes or {}).get(key)


def _fmt_money(value) -> str | None:
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


def build_outlook(events: list, today: date | None = None) -> EarningsOutlook:
    """Fold a company's earnings events into the read for the next one."""
    today = today or date.today()

    upcoming = [e for e in events if not _attr(e, "reported") and e.as_of_date >= today]
    reported = [e for e in events if _attr(e, "reported")]

    nxt = min(upcoming, key=lambda e: e.as_of_date) if upcoming else None
    days_until = (nxt.as_of_date - today).days if nxt else None

    surprises = [
        float(_attr(e, "eps_surprise_percent"))
        for e in reported
        if _attr(e, "eps_surprise_percent") is not None
    ]
    beats = sum(1 for s in surprises if s > 0)
    misses = sum(1 for s in surprises if s < 0)
    average = round(sum(surprises) / len(surprises), 1) if surprises else None
    last = surprises[-1] if surprises else None

    quarter_label = None
    if nxt is not None:
        q, fy = _attr(nxt, "quarter"), _attr(nxt, "fiscal_year")
        if q and fy:
            quarter_label = f"Q{q} {fy}"

    return EarningsOutlook(
        next_date=nxt.as_of_date if nxt else None,
        days_until=days_until,
        when_label=_attr(nxt, "hour_label") if nxt else None,
        eps_estimate=_attr(nxt, "eps_estimate") if nxt else None,
        revenue_estimate=_attr(nxt, "revenue_estimate") if nxt else None,
        quarter_label=quarter_label,
        is_imminent=days_until is not None and days_until <= IMMINENT_DAYS,
        reports_seen=len(surprises),
        beats=beats,
        misses=misses,
        average_surprise_percent=average,
        last_surprise_percent=last,
        headline=_headline(nxt, days_until, surprises, beats, quarter_label),
    )


def _headline(nxt, days_until, surprises, beats, quarter_label) -> str:
    """One plain sentence about the next report."""
    if nxt is None:
        return "No scheduled earnings date is known for this company."

    when = _attr(nxt, "hour_label")
    when_phrase = f" {when}" if when else ""
    quarter = f"{quarter_label} " if quarter_label else ""

    if days_until == 0:
        timing = f"Reports {quarter}today{when_phrase}"
    elif days_until == 1:
        timing = f"Reports {quarter}tomorrow{when_phrase}"
    else:
        timing = f"Reports {quarter}in {days_until} days, on {nxt.as_of_date:%d %b}"

    expectation = ""
    eps, revenue = _attr(nxt, "eps_estimate"), _fmt_money(_attr(nxt, "revenue_estimate"))
    if eps is not None and revenue:
        expectation = f". The market expects ${float(eps):.2f} per share on {revenue} of revenue"
    elif eps is not None:
        expectation = f". The market expects ${float(eps):.2f} per share"

    record = ""
    if len(surprises) >= MIN_HISTORY_FOR_RECORD:
        record = f". It has come in ahead of expectations {beats} of the last {len(surprises)} times"

    return f"{timing}{expectation}{record}."
