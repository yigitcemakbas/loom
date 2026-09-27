"""What the price has already done, so Loom can tell what matters from what does not.

Loom reads a filing and forms a view. The market read the same filing, and has
already acted or declined to. That reaction is the single most useful piece of
context a finding carries, and until now it reached only two places: the prompt
that builds a standing prior, and the earnings tape. It never reached the two
surfaces that decide what a reader actually sees, which are the stance and the
ranking in the case file. Loom could describe margin pressure in detail while
having no idea the shares had already fallen a fifth on it.

**This does not make the engine predictive, and the distinction is the whole
design.** Refusing to forecast a price is a position about what Loom can
honestly claim. Refusing to *look* at the price throws away the only
independent check there is on whether a disclosure mattered. Everything here is
arithmetic over sessions that have already closed. It states what happened.

Three things it is allowed to decide, and one it is not.

**It may say whether a disclosure was material.** A finding followed by a move
several times the size this company normally makes is one an independent party
agreed was worth repricing. That is confirmation from outside Loom, and it
lifts the finding in the ranking.

**It may say where the price stands.** The same risk factor means different
things at an all-time high and after a forty percent fall, and nothing in the
filing says which situation the reader is in.

**It may disagree.** Loom's reading and the tape are independent sources, so
when one says serious and the other did not blink, that is the shape Loom
treats as most valuable: not a conclusion, a reason to look.

**It may not vote on direction.** Letting the price decide which way the stance
leans is momentum wearing a verdict's clothes, and it is predictive in exactly
the sense the project refuses. There is a subtler version of the same mistake
that is easier to walk into: discounting a finding *because* the market ignored
it. That reads as calibration and is not. A disclosure the market has not
priced is the only kind Loom can add anything to, and a rule that quietly
suppressed those would delete the reason to read filings at all. So reaction
confirms, and silence is reported without penalty.

**Materiality is company-relative, and must be.** A four percent fortnight is
an enormous move for a utility and an ordinary one for a small semiconductor
company. A single threshold across every ticker would find events in the
volatile names and nothing anywhere else, which is a measure of volatility
wearing the name of a measure of news. So a move is expressed in units of what
*this* company normally does over the same span.
"""

from dataclasses import dataclass
from datetime import date, timedelta
from math import sqrt
from statistics import pstdev
from typing import Optional

from app.engine.quant.prices import PriceHistory

# How long after a disclosure the market is given to respond. Roughly two
# trading weeks: long enough for something buried on page ninety of a filing to
# be read and priced, short enough that the move is still attributable to it.
REACTION_SESSIONS = 10

# How much history the company's own typical move is measured over. Six months:
# long enough that a single event does not set the baseline, short enough to
# follow a company whose volatility has genuinely changed.
VOLATILITY_SESSIONS = 126

# Below this many observations the baseline is not reported at all. A standard
# deviation from eight sessions is a number, not a description.
MIN_VOLATILITY_OBSERVATIONS = 30

# A move this many times the company's own typical move counts as the market
# having responded, and at the higher figure as having responded hard. Stated
# judgements rather than fitted values: they decide what a reader is shown
# first, and nothing here is multiplied by a return or claims significance.
MATERIAL_SIGMA = 1.5
STRIKING_SIGMA = 3.0

# Where in its 52 week range a price stops being unremarkable.
NEAR_HIGH = 0.9
NEAR_LOW = 0.1

# A fall from the high worth naming. Below this it is ordinary movement.
DEEP_DRAWDOWN = 0.20

# Below this many peers the group is not a sector, it is a handful of
# companies, and a figure computed from three of them presented as an industry
# invites a reader to conclude something from a coincidence.
MIN_PEERS = 5

_SESSIONS_PER_YEAR = 252
_WINDOWS = {"1m": 21, "3m": 63, "6m": 126, "1y": 252}


@dataclass(frozen=True)
class Move:
    """How the price moved after something, measured against the market."""

    # The company's return minus the benchmark's over the same sessions. Raw
    # return would call a market-wide selloff a reaction to this company's
    # filing, which is the error the whole measure exists to avoid.
    abnormal_percent: float
    sessions: int
    # The session the move was measured from, so a caller holding several can
    # tell which filing each belongs to without keeping a parallel index.
    as_of: date
    # The move in units of what this company normally does over the same span.
    # None where there is too little history to say what normal is, which is
    # not the same as the move being ordinary.
    sigma: Optional[float]
    summary: str

    @property
    def is_material(self) -> bool:
        return self.sigma is not None and abs(self.sigma) >= MATERIAL_SIGMA

    @property
    def is_striking(self) -> bool:
        return self.sigma is not None and abs(self.sigma) >= STRIKING_SIGMA


    @property
    def short(self) -> str:
        """A clause for a row, rather than a sentence for a paragraph.

        One annual report yields forty findings and every one of them measures
        the same fortnight, so the full sentence appeared forty times on a
        single page. The fact is worth carrying on each row and is not worth
        forty repetitions of a paragraph, so the row gets a clause and the page
        states it once. Written to follow the word "from", which is how the
        interface introduces a source.
        """
        direction = "rose" if self.abnormal_percent > 0 else "fell"
        return (
            f"after which the shares {direction} "
            f"{abs(self.abnormal_percent):.1f}% against the market"
        )

    @property
    def standalone(self) -> str:
        """The same fact as a sentence that names its own subject.

        `summary` is written to sit under a finding, where "this" refers to the
        finding above it. On its own, in a block of its own, "this" refers to
        nothing.
        """
        direction = "rising" if self.abnormal_percent > 0 else "falling"
        tail = ""
        if self.sigma is not None and abs(self.sigma) >= STRIKING_SIGMA:
            tail = f", about {abs(self.sigma):.1f} times a normal fortnight for this company"
        elif self.sigma is not None and abs(self.sigma) >= MATERIAL_SIGMA:
            tail = ", a larger move than this company usually makes"
        return (
            f"The most recent filing Loom read here was followed by the shares "
            f"{direction} {abs(self.abnormal_percent):.1f}% against the market{tail}."
        )


@dataclass(frozen=True)
class SectorMove:
    """What the rest of the sector did over the same window.

    The channel the company-level measure is structurally blind to. A move is
    reported there as the company's return *minus the market's*, which is right
    for "did this filing move this company" and removes, by construction, any
    effect that hit a whole group. If one company writes something about AI
    capital spending and the entire complex reprices, every company in it shows
    an abnormal return near zero and the measure records that nothing happened.

    So this measures the peers and leaves the filer out of them. Two numbers,
    because they answer different questions: whether the group moved, and
    whether the filer moved differently from its group.
    """

    # The equal-weighted peer group's return minus the market's.
    peers_percent: float
    # The filer's return minus its own peer group's. Positive means it did
    # better than the companies it sits with, whatever the group did.
    versus_peers_percent: float
    peers: int
    sector: str
    sessions: int

    @property
    def moved_together(self) -> bool:
        """Whether this looks like a sector event rather than a company one.

        A deliberately weak test, and it claims nothing about cause. Plenty of
        things move a sector on a given fortnight and a filing is only one of
        them; what this distinguishes is whether the reader is looking at
        something that happened to one company or to all of them.
        """
        return abs(self.peers_percent) >= abs(self.versus_peers_percent)

    @property
    def summary(self) -> str:
        moved = "rose" if self.peers_percent > 0 else "fell"
        relative = (
            "more than" if self.versus_peers_percent > 0 else "less than"
        )
        return (
            f"Over the same fortnight the other {self.peers} {self.sector.lower()} "
            f"companies Loom follows {moved} {abs(self.peers_percent):.1f}% against the "
            f"market, so this company moved {abs(self.versus_peers_percent):.1f}% "
            f"{relative} the group it sits in."
        )


@dataclass(frozen=True)
class Standing:
    """Where the price is now, and how it got there."""

    last_close: float
    change_1m: Optional[float]
    change_3m: Optional[float]
    change_1y: Optional[float]
    # 0.0 at the 52 week low, 1.0 at the high. The most compact answer to
    # whether a company is beaten down or priced for perfection.
    range_position: Optional[float]
    # How far below the 52 week high, as a positive fraction.
    drawdown: Optional[float]
    summary: str

    @property
    def near_high(self) -> bool:
        return self.range_position is not None and self.range_position >= NEAR_HIGH

    @property
    def near_low(self) -> bool:
        return self.range_position is not None and self.range_position <= NEAR_LOW


def _abnormal_returns(
    history: PriceHistory, benchmark: Optional[PriceHistory], start: date, end: date
) -> list[float]:
    """The company's daily returns with the market's removed, where possible."""
    own = history.returns_by_date(start, end)
    if benchmark is None:
        return list(own.values())
    market = benchmark.returns_by_date(start, end)
    # Only sessions both traded. A day the company was halted is not a day it
    # outperformed by the whole of the market's fall.
    return [own[day] - market[day] for day in own if day in market]


def typical_move(
    history: PriceHistory,
    benchmark: Optional[PriceHistory],
    as_of: date,
    *,
    sessions: int = REACTION_SESSIONS,
    lookback: int = VOLATILITY_SESSIONS,
) -> Optional[float]:
    """What a move of `sessions` length normally looks like for this company.

    Measured strictly before `as_of`, so the baseline a move is judged against
    cannot contain the move itself. Scaled by the square root of the window,
    which is how a per-session dispersion becomes a per-fortnight one.
    """
    start = as_of - timedelta(days=int(lookback * 1.6))
    daily = _abnormal_returns(history, benchmark, start, as_of - timedelta(days=1))
    if len(daily) < MIN_VOLATILITY_OBSERVATIONS:
        return None
    per_session = pstdev(daily)
    if per_session <= 0:
        return None
    return per_session * sqrt(sessions)


def move_after(
    history: Optional[PriceHistory],
    benchmark: Optional[PriceHistory],
    when: date,
    *,
    sessions: int = REACTION_SESSIONS,
) -> Optional[Move]:
    """How the price responded in the sessions after a disclosure.

    Returns None rather than zero when the history cannot answer. A company
    whose prices Loom does not hold has not had a flat reaction, it has had an
    unobserved one, and the two must not render alike.
    """
    if history is None:
        return None

    # Sessions are not calendar days. The span is padded so that ten sessions
    # of trading are actually available inside it, weekends and holidays
    # included, and then the return is taken over the dates themselves.
    end = when + timedelta(days=int(sessions * 1.6) + 4)
    own = history.total_return(when, end)
    if own is None:
        return None

    market = benchmark.total_return(when, end) if benchmark is not None else None
    abnormal = own - market if market is not None else own
    percent = round(abnormal * 100, 2)

    baseline = typical_move(history, benchmark, when, sessions=sessions)
    sigma = round(abnormal / baseline, 2) if baseline else None

    return Move(
        abnormal_percent=percent,
        sessions=sessions,
        as_of=when,
        sigma=sigma,
        summary=_describe_move(percent, sigma, benchmark is not None),
    )


def _describe_move(percent: float, sigma: Optional[float], adjusted: bool) -> str:
    """The move, worded as a fact about the filing rather than about the finding.

    The distinction is not pedantry. One annual report yields forty findings and
    they all carry the same date, so they all measure the same fortnight. Saying
    "the price fell 15% after this" against each of them in turn tells a reader
    forty times that one particular risk factor moved the market, which is a
    causal claim about forty different things and cannot be true of any of them
    individually. Saying the *filing* was followed by the move is what actually
    happened, and it is still the useful fact: a document the market repriced
    is a document worth reading closely.
    """
    direction = "fell" if percent < 0 else "rose"
    against = " against the market" if adjusted else ""
    base = (
        f"In the fortnight after this was filed, the shares {direction} "
        f"{abs(percent):.1f}%{against}"
    )

    if sigma is None:
        # Said rather than left out. Not enough history to judge is a different
        # statement from an ordinary move, and a reader must be able to tell.
        return f"{base}. Loom holds too little history here to say whether that is unusual."
    if abs(sigma) >= STRIKING_SIGMA:
        return f"{base}, about {abs(sigma):.1f} times a normal fortnight for this company."
    if abs(sigma) >= MATERIAL_SIGMA:
        return f"{base}, a larger move than this company usually makes."
    return f"{base}, an ordinary move for this company."


def sector_move_after(
    history: Optional[PriceHistory],
    peers: list[PriceHistory],
    benchmark: Optional[PriceHistory],
    when: date,
    sector: str,
    *,
    sessions: int = REACTION_SESSIONS,
    minimum_peers: int = MIN_PEERS,
) -> Optional[SectorMove]:
    """How the filer's peers moved, and how the filer moved against them.

    Equal weighted, deliberately. A capitalisation-weighted group of the
    companies Loom follows would be two or three names and their sector's name
    on it, which answers a question about those names rather than about the
    group.

    Refuses below `minimum_peers`. Three companies are not a sector, and a
    number computed from three that is presented as one invites a reader to
    conclude something about an industry from a coincidence.
    """
    if history is None or len(peers) < minimum_peers:
        return None

    end = when + timedelta(days=int(sessions * 1.6) + 4)
    own = history.total_return(when, end)
    market = benchmark.total_return(when, end) if benchmark is not None else None
    if own is None or market is None:
        return None

    measured = [r for peer in peers if (r := peer.total_return(when, end)) is not None]
    if len(measured) < minimum_peers:
        return None

    group = sum(measured) / len(measured)
    return SectorMove(
        peers_percent=round((group - market) * 100, 2),
        versus_peers_percent=round((own - group) * 100, 2),
        peers=len(measured),
        sector=sector,
        sessions=sessions,
    )


def standing(history: Optional[PriceHistory], as_of: date) -> Optional[Standing]:
    """Where the price sits, as at a date. No forecast, only what has happened."""
    if history is None or len(history) < 2:
        return None
    last = history.on_or_before(as_of)
    if last is None:
        return None

    def change(sessions: int) -> Optional[float]:
        then = as_of - timedelta(days=int(sessions * 1.45))
        if not history.covers(as_of, back_days=int(sessions * 1.45)):
            return None
        total = history.total_return(then, as_of)
        return round(total * 100, 2) if total is not None else None

    window = history.between(as_of - timedelta(days=365), as_of)
    position = drawdown = None
    if len(window) >= MIN_VOLATILITY_OBSERVATIONS:
        closes = [bar.adjusted_close for bar in window]
        low, high = min(closes), max(closes)
        here = window[-1].adjusted_close
        span = high - low
        position = round((here - low) / span, 3) if span > 0 else None
        drawdown = round(1 - here / high, 3) if high > 0 else None

    changes = {name: change(n) for name, n in _WINDOWS.items()}
    return Standing(
        last_close=round(last.close, 2),
        change_1m=changes["1m"],
        change_3m=changes["3m"],
        change_1y=changes["1y"],
        range_position=position,
        drawdown=drawdown,
        summary=_describe_standing(changes, position, drawdown),
    )


def _describe_standing(
    changes: dict[str, Optional[float]],
    position: Optional[float],
    drawdown: Optional[float],
) -> str:
    """One sentence a reader can act on without knowing any vocabulary."""
    parts: list[str] = []
    year = changes.get("1y")
    month = changes.get("1m")
    if year is not None:
        parts.append(f"{'up' if year >= 0 else 'down'} {abs(year):.0f}% over the past year")
    if month is not None:
        parts.append(f"{'up' if month >= 0 else 'down'} {abs(month):.0f}% over the past month")

    where = ""
    if position is not None and position >= NEAR_HIGH:
        where = (
            " It is trading close to its highest point of the past year, so little bad news "
            "is currently reflected in the price."
        )
    elif drawdown is not None and drawdown >= DEEP_DRAWDOWN:
        where = (
            f" It is about {drawdown * 100:.0f}% below its high of the past year, so some "
            f"bad news is already reflected in the price."
        )

    if not parts:
        return "Loom holds too little price history for this company to say how it has moved."
    return f"The shares are {' and '.join(parts)}.{where}"


def moves_for(
    signals: list,
    history: Optional[PriceHistory],
    benchmark: Optional[PriceHistory],
    *,
    sessions: int = REACTION_SESSIONS,
) -> dict[str, Move]:
    """The market's response to each finding's document, keyed by finding id.

    Computed once per distinct date rather than once per finding. Forty findings
    from one annual report measure the same fortnight, and recomputing a
    standard deviation over six months of history forty times to reach the same
    answer is work done to no purpose.
    """
    if history is None:
        return {}
    cache: dict[date, Optional[Move]] = {}
    found: dict[str, Move] = {}
    for signal in signals:
        occurred = getattr(signal, "occurred_at", None)
        if occurred is None:
            continue
        when = occurred.date()
        if when not in cache:
            cache[when] = move_after(history, benchmark, when, sessions=sessions)
        move = cache[when]
        if move is not None:
            found[str(getattr(signal, "id", id(signal)))] = move
    return found


__all__ = [
    "DEEP_DRAWDOWN",
    "MATERIAL_SIGMA",
    "MIN_VOLATILITY_OBSERVATIONS",
    "NEAR_HIGH",
    "NEAR_LOW",
    "REACTION_SESSIONS",
    "STRIKING_SIGMA",
    "VOLATILITY_SESSIONS",
    "MIN_PEERS",
    "Move",
    "SectorMove",
    "Standing",
    "move_after",
    "moves_for",
    "sector_move_after",
    "standing",
    "typical_move",
]
