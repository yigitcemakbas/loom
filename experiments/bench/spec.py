"""The design, in one place, so the run cannot drift from what was registered.

Every constant a result depends on lives here rather than at its point of use.
The orchestrator reads this module and nothing else to decide what to run.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# --------------------------------------------------------------- arms

CORE_ARMS = ("control", "evidence", "full")
MECHANISM_ARMS = ("verdict_only", "placebo", "no_factor", "no_price",
                  "no_contradiction", "degraded")
TIER2_ARMS = ("control", "quant_loom")
ALL_ARMS = tuple(dict.fromkeys(CORE_ARMS + MECHANISM_ARMS + TIER2_ARMS))

# Arms built by subtracting a block from `evidence`, for component attribution.
ABLATIONS = {"no_factor": "factors", "no_price": "price", "no_contradiction": "contradictions"}

# --------------------------------------------------------------- readers

@dataclass(frozen=True)
class Persona:
    key: str
    system: str


# The personas are deliberately not four flavours of the same caution. The
# gambler exists because a research product that only helps the careful is a
# product that helps whoever needed it least, and the earlier trial found the
# largest effect of Loom's verdict on exactly this reader.
PERSONAS = (
    Persona("professional", (
        "You are a buy-side equity analyst with fifteen years of experience. "
        "You size positions against conviction and you are comfortable being "
        "wrong on a quarter if the process is sound. You short when the "
        "evidence warrants it. You do not hold cash for its own sake, but you "
        "will not deploy capital into a name you cannot justify."
    )),
    Persona("amateur", (
        "You are a retail investor with a few years of experience. You know "
        "the basic vocabulary but you are not trained in accounting or "
        "statistics. You are nervous about losing money and you tend to trust "
        "what you are told by sources that sound authoritative."
    )),
    Persona("gambler", (
        "You are an aggressive speculator. You are hunting for large gains "
        "over the next quarter and you accept the possibility of large losses. "
        "You concentrate, you use shorts, and you consider holding cash to be "
        "a wasted opportunity."
    )),
)
PERSONAS_BY_KEY = {p.key: p for p in PERSONAS}

# Seeds vary temperature and the order names appear in the packet. Order is
# varied because a list is a prior: whatever sits at the top of a prompt gets
# read most carefully, and an effect that only survives one ordering is an
# artefact of the ordering.
SEEDS = (1, 2)      # dates are preferred over seeds when budget binds
TEMPERATURE_BY_SEED = {1: 0.2, 2: 0.7, 3: 1.0}

READER_MODEL = "gemini-3.6-flash"
ALT_READER_MODEL = "gemini-3.5-flash"   # kept for the cell name suffix logic
READER_MODELS = ("gemini-3.6-flash", "gemini-3.5-flash", "gemini-3.7-flash")
DAILY_CALLS_PER_MODEL = 20

# --------------------------------------------------------------- periods

@dataclass(frozen=True)
class Window:
    """A decision date and how long the book is held, counted in sessions so
    a short final window is honest about being short rather than padded."""
    decide: date
    sessions: int
    label: str


# Findings cluster in 2026-02 and 2026-07/08. Each date sits just after a
# cluster so the treatment has something to say; the last one is three weeks
# from the end of the price history and is measured over 21 sessions only.
TIER1_WINDOWS = (
    Window(date(2026, 3, 2), 63, "2026-03-02_63d"),
    Window(date(2026, 3, 2), 126, "2026-03-02_126d"),
    Window(date(2026, 6, 25), 63, "2026-06-25_63d"),
    Window(date(2026, 9, 1), 17, "2026-09-01_17d"),
)
# Only these carry the primary endpoint: 63 sessions is the registered horizon.
PRIMARY_WINDOWS = tuple(w for w in TIER1_WINDOWS if w.sessions == 63)

# Tier 2 exists to see a bear market. Dates chosen for regime, not outcome:
# each is a quarter boundary or an index turning point identifiable without
# knowing what followed. The regime label is assigned afterwards from realised
# benchmark return and volatility, never asserted here.
TIER2_WINDOWS = (
    Window(date(2020, 2, 19), 63, "2020-02-19_63d"),
    Window(date(2020, 6, 30), 63, "2020-06-30_63d"),
    Window(date(2021, 6, 30), 63, "2021-06-30_63d"),
    Window(date(2022, 1, 3), 63, "2022-01-03_63d"),
    Window(date(2022, 6, 30), 63, "2022-06-30_63d"),
    Window(date(2023, 1, 3), 63, "2023-01-03_63d"),
    Window(date(2024, 6, 28), 63, "2024-06-28_63d"),
    Window(date(2025, 3, 31), 63, "2025-03-31_63d"),
)

# --------------------------------------------------------------- universes

TIER1_COVERED = 28        # companies Loom has read at the decision date
TIER1_UNCOVERED = 12      # companies it has not, to test where it is silent
TIER1_MIN_FINDINGS = 3
TIER2_SIZE = 60           # drawn across size strata

BENCHMARK = "QQQ"

# --------------------------------------------------------------- statistics

BOOTSTRAP_RESAMPLES = 10_000
BOOTSTRAP_BLOCK = 5       # expected block length in sessions
CONFIDENCE = 0.95
BH_ALPHA = 0.05

# Trading-day convention for annualising. 252 everywhere, stated once.
SESSIONS_PER_YEAR = 252

# --------------------------------------------------------------- the grid

READER_ARMS = ("control", "evidence", "full", "verdict_only")
CORE_READER_ARMS = READER_ARMS[:3]

READER_WINDOWS = (
    # Fixed by an availability rule before any outcome was inspected: every
    # month-start from March 2026 that has at least 35 companies with three or
    # more findings visible, plus the quarter boundary, and a full 63-session
    # forward window inside the stored price history. February 2026 is excluded
    # because only 11 companies qualified; July and August are excluded because
    # the price tape ends on 2026-09-25 and cannot complete their horizon.
    #
    # The dates overlap, which is a property of the corpus rather than a choice:
    # Loom's document layer begins in October 2025. Overlap is handled in the
    # statistics and stated as a limitation, not hidden.
    Window(date(2026, 3, 2), 63, "2026-03-02_63d"),
    Window(date(2026, 4, 1), 63, "2026-04-01_63d"),
    Window(date(2026, 5, 1), 63, "2026-05-01_63d"),
    Window(date(2026, 6, 1), 63, "2026-06-01_63d"),
    Window(date(2026, 6, 25), 63, "2026-06-25_63d"),
)


@dataclass(frozen=True)
class Cell:
    """One book to produce. The filename derives from this and is the unit of
    resumability: if the file exists, the cell is done."""
    tier: int
    arm: str
    persona: str
    seed: int
    window: Window
    model: str = READER_MODEL

    @property
    def name(self) -> str:
        return (f"t{self.tier}__{self.window.label}__{self.persona}__{self.arm}"
                f"__seed{self.seed}__{self.model.replace('.', '-')}")


def _cells() -> list[tuple[str, int, Window]]:
    """Every (persona, seed, date) pairing cell, core seeds first.

    Ordered this way so that an interrupted run has completed whole cells rather
    than fragments: a cell missing its control contributes nothing to a paired
    contrast, so a half-finished cell is wasted quota.
    """
    out = []
    for seed_group in ((1, 2), (3,)):
        for w in READER_WINDOWS:
            for p in PERSONAS:
                for s in seed_group:
                    out.append((p.key, s, w))
    return out


def grid() -> list[Cell]:
    """The reader grid: 90 books, ordered so any prefix is a valid experiment.

    Every arm inside one cell shares that cell's model, so the paired contrast
    is never a model comparison in disguise. Models rotate across cells, which
    makes model a blocking factor and lets the run use all three daily quotas.
    """
    cells: list[Cell] = []
    # The four core arms across every cell first, then the fifth arm. Losing
    # verdict_only costs one secondary hypothesis; losing a control costs a
    # whole cell.
    for arms in (CORE_READER_ARMS, READER_ARMS[4:]):
        for i, (persona, seed, w) in enumerate(_cells()):
            model = READER_MODELS[i % len(READER_MODELS)]
            for a in arms:
                cells.append(Cell(1, a, persona, seed, w, model=model))
    return cells
