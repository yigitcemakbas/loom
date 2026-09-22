"""What moved since last time.

Loom holds a hundred and twenty-nine opinions and, until now, said nothing when
one of them changed. That is the difference between a reference work and an
instrument: a reference is something you consult when you already suspect
something, and an instrument tells you when to look. A tool that requires you
to remember to check every company every week is a tool you will stop checking.

The hard part is not diffing. It is deciding what counts as a change, because
a feed that reports everything is the same as a feed that reports nothing: a
reader who sees forty rows learns to skim, and the one row that mattered is
lost in the other thirty-nine. So every rule here has a threshold, and the
thresholds are set where the move is large enough that a person would want to
know rather than where it is merely detectable.

Three kinds of change, in descending order of how much they should interrupt
someone:

  a verdict flipped        Loom's own conclusion about a company reversed
  a factor crossed a tail  a reading entered or left the extremes of its peers
  a filing scored notable  the fast path matched a standing expectation

Nothing here re-derives an opinion. It compares two stored states, which means
it is arithmetic, costs nothing, and cannot disagree with what the rest of the
product is showing.
"""

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.engine.brief import STANCE_LABELS
from app.engine.quant.factors import FACTORS_BY_KEY
from app.models.brief import CompanyBrief, Stance
from app.models.company import Company
from app.models.event_assessment import EventAssessment
from app.models.factor import COMPOSITE_KEY, FactorScore

# A factor has to cross into or out of a tail to count. A reading drifting from
# the 44th to the 52nd percentile is noise dressed as news, and reporting it
# would bury the reading that went from the 60th to the 4th.
TAIL_LOW = 0.15
TAIL_HIGH = 0.85

# Stances ordered so a "flip" can be distinguished from a softening. Moving
# from strong negative to negative is Loom becoming less sure; moving from
# negative to positive is Loom changing its mind, and only the second is news.
_STANCE_RANK = {
    Stance.STRONG_NEGATIVE: -2,
    Stance.NEGATIVE: -1,
    Stance.MIXED: 0,
    Stance.QUIET: 0,
    Stance.INSUFFICIENT: 0,
    Stance.POSITIVE: 1,
    Stance.STRONG_POSITIVE: 2,
}

# How loud each kind of change is, used only for ordering a feed.
SEVERITY = {"verdict": 3, "factor": 2, "event": 1}


@dataclass(frozen=True)
class Change:
    ticker: str
    kind: str          # "verdict" | "factor" | "event"
    headline: str
    detail: str
    occurred_at: datetime
    # Present for factor changes, so the interface can link to the measure.
    factor_key: Optional[str] = None

    @property
    def severity(self) -> int:
        return SEVERITY.get(self.kind, 0)


def _crossed(previous: float, current: float) -> Optional[str]:
    """Which tail a reading entered or left, or None if it stayed put.

    Movement *within* the middle is deliberately not a change. A factor is
    interesting when it becomes extreme or stops being extreme, not when it
    wanders around the median.
    """
    was_low, is_low = previous <= TAIL_LOW, current <= TAIL_LOW
    was_high, is_high = previous >= TAIL_HIGH, current >= TAIL_HIGH
    if is_low and not was_low:
        return "entered_low"
    if is_high and not was_high:
        return "entered_high"
    if was_low and not is_low:
        return "left_low"
    if was_high and not is_high:
        return "left_high"
    return None


def verdict_changes(db: Session, *, since: datetime) -> list[Change]:
    """Companies whose stance reversed direction.

    A softening is not reported. Loom moving from "serious concerns" to
    "leaning negative" is the same opinion held less firmly, and a feed that
    treats it as news teaches a reader that the feed is noisy.
    """
    rows = db.execute(
        select(CompanyBrief, Company.ticker)
        .join(Company, Company.id == CompanyBrief.company_id)
        .where(CompanyBrief.generated_at >= since - timedelta(days=90))
        .order_by(CompanyBrief.company_id, CompanyBrief.generated_at)
    ).all()

    latest_by_company: dict[str, list] = {}
    for brief, ticker in rows:
        latest_by_company.setdefault(ticker, []).append(brief)

    changes: list[Change] = []
    for ticker, briefs in latest_by_company.items():
        current = briefs[-1]
        if current.generated_at < since:
            continue
        # The most recent brief that says something different, rather than the
        # immediately preceding one: regenerating briefs on a schedule produces
        # runs of identical stances, and comparing against the neighbour would
        # miss a change that happened two runs ago.
        previous = next(
            (b for b in reversed(briefs[:-1]) if b.stance != current.stance), None
        )
        if previous is None:
            continue

        before = _STANCE_RANK.get(previous.stance, 0)
        after = _STANCE_RANK.get(current.stance, 0)
        if before == 0 and after == 0:
            continue
        if (before > 0) == (after > 0) and before != 0 and after != 0:
            # Same side, different strength. Not a change of mind.
            continue

        # The headline states the move, not the fact that a move happened.
        # "Loom changed its mind" is true of every row in this group, so using
        # it as the headline spends the most valuable line on the page saying
        # what the group heading already said, and buries the actual verdict in
        # the detail underneath.
        was = STANCE_LABELS.get(previous.stance, previous.stance.value).lower()
        now = STANCE_LABELS.get(current.stance, current.stance.value).lower()
        changes.append(Change(
            ticker=ticker,
            kind="verdict",
            headline=f"{ticker}: now {now}, was {was}.",
            detail=current.headline,
            occurred_at=current.generated_at,
        ))
    return changes


def factor_changes(db: Session, *, since: datetime) -> list[Change]:
    """Readings that entered or left the extremes of their peer group."""
    dates = list(db.execute(
        select(FactorScore.as_of_date).distinct().order_by(FactorScore.as_of_date.desc()).limit(2)
    ).scalars())
    if len(dates) < 2:
        # One scoring run in the database is a starting point, not a change.
        return []
    current_date, previous_date = dates[0], dates[1]
    if datetime.combine(current_date, datetime.min.time(), tzinfo=timezone.utc) < since:
        return []

    def _scores(when: date) -> dict[tuple[str, str], float]:
        rows = db.execute(
            select(FactorScore, Company.ticker)
            .join(Company, Company.id == FactorScore.company_id)
            .where(FactorScore.as_of_date == when)
        ).all()
        return {
            (ticker, score.factor_key): score.percentile
            for score, ticker in rows
            if score.percentile is not None
        }

    now, before = _scores(current_date), _scores(previous_date)
    moment = datetime.combine(current_date, datetime.min.time(), tzinfo=timezone.utc)

    changes: list[Change] = []
    for (ticker, key), percentile in now.items():
        was = before.get((ticker, key))
        if was is None:
            continue
        direction = _crossed(was, percentile)
        if direction is None:
            continue

        factor = FACTORS_BY_KEY.get(key)
        label = factor.label if factor else key.replace("_", " ")
        if key == COMPOSITE_KEY:
            label = "Overall reported numbers"

        if direction == "entered_low":
            headline = f"{ticker}: {label} fell into the worst of its peers."
        elif direction == "entered_high":
            headline = f"{ticker}: {label} rose into the best of its peers."
        elif direction == "left_low":
            headline = f"{ticker}: {label} recovered out of the worst of its peers."
        else:
            headline = f"{ticker}: {label} dropped out of the best of its peers."

        changes.append(Change(
            ticker=ticker,
            kind="factor",
            headline=headline,
            detail=(
                f"{label} moved from the {round(was * 100)}th to the "
                f"{round(percentile * 100)}th percentile of comparable companies. "
                + (factor.meaning if factor else "")
            ).strip(),
            occurred_at=moment,
            factor_key=key,
        ))
    return changes


def event_changes(db: Session, *, since: datetime, min_score: float = 1.0) -> list[Change]:
    """Filings the fast path judged notable against a standing expectation.

    Collapsed by company and by which expectations were matched. News coverage
    of one development arrives as a dozen separate items within a few hours,
    and each one matches the same standing concern. Reported individually they
    filled most of the feed with the same sentence about the same company, told
    twelve times, which is the failure this module's thresholds exist to
    prevent and which the first version of this function walked straight into.

    The count is kept and shown, because "twelve separate items matched this"
    is real information about how hard a story is landing. What is not
    information is twelve rows.
    """
    rows = db.execute(
        select(EventAssessment, Company.ticker)
        .join(Company, Company.id == EventAssessment.company_id)
        .where(EventAssessment.occurred_at >= since)
        .where(EventAssessment.score >= min_score)
        .order_by(EventAssessment.occurred_at.desc())
    ).all()

    # Keyed on the company and the set of expectations matched, so two
    # genuinely different developments at one company stay separate while a
    # dozen retellings of one collapse.
    grouped: dict[tuple, list] = {}
    for assessment, ticker in rows:
        topics = tuple(sorted(m.get("topic", "") for m in (assessment.matches or [])))
        grouped.setdefault((ticker, topics), []).append(assessment)

    changes: list[Change] = []
    for (ticker, _topics), group in grouped.items():
        # The highest-scoring member speaks for the group, not the newest: the
        # 8-K that moved the story matters more than the last blog post about it.
        lead = max(group, key=lambda a: a.score)
        newest = max(group, key=lambda a: a.occurred_at)
        forms = sorted({a.form for a in group if a.form})

        detail = (
            f"A {lead.form or 'filing'} matched what Loom was already watching "
            f"for at this company."
        )
        if len(group) > 1:
            detail = (
                f"{len(group)} separate items matched the same standing expectation"
                + (f" ({', '.join(forms)})" if forms else "")
                + ". The strongest is quoted above."
            )

        changes.append(Change(
            ticker=ticker,
            kind="event",
            # The assessment headline already opens with the ticker, so
            # prefixing it again produced "AAPL: AAPL: ...".
            headline=lead.headline,
            detail=detail,
            occurred_at=newest.occurred_at,
        ))
    return changes


def recent_changes(db: Session, *, days: int = 7, limit: int = 60) -> list[Change]:
    """Everything worth interrupting someone about, loudest first.

    Ordered by kind before recency on purpose. A verdict reversing is worth
    more of a reader's attention than a filing that matched a keyword, even if
    the filing is newer, and a feed sorted purely by time buries the first
    under the second.
    """
    since = datetime.now(timezone.utc) - timedelta(days=days)
    changes = (
        verdict_changes(db, since=since)
        + factor_changes(db, since=since)
        + event_changes(db, since=since)
    )
    changes.sort(key=lambda c: (c.severity, c.occurred_at), reverse=True)
    return changes[:limit]


__all__ = [
    "SEVERITY",
    "TAIL_HIGH",
    "TAIL_LOW",
    "Change",
    "event_changes",
    "factor_changes",
    "recent_changes",
    "verdict_changes",
]
