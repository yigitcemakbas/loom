"""Evidence that is older than it looks.

Every date Loom stores is correct, and that is the problem this module exists
for. A finding carries the date of the document it came from, so a reader
checking whether a verdict is current sees a recent timestamp and stops there.
Two ways that reassurance can be wrong, and neither is visible to any check
Loom ran before this:

**The quote is about an older period than the document.** A filing published
this month can quote a call from two years ago, describe a fiscal year that
closed long before, or repeat a figure from an earlier report. The finding's
date is right, the finding's content is not current, and nothing in the record
distinguishes the two. Measured on the stored corpus, Tesla's optimistic side
rested on a 2024 earnings call while its pessimistic side was current, which no
reader could have seen from the interface: both sides showed recent dates.

**The two sides of a verdict are not the same age.** A case built from current
concerns and year-old positives is not a balanced case, it is a stale
comparison. This one is computable from timestamps alone and was simply never
asked.

The detection is deterministic and deliberately conservative. A company's own
annual report legitimately talks about the year it covers, so naming last year
is normal and only naming a year *before* last is evidence of staleness. It is
better to miss a stale quote than to put a warning on every 10-K, because a
warning that appears everywhere is read nowhere.
"""

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from statistics import median
from typing import Optional

# How far behind a finding's own date the newest year it names has to be before
# the content counts as stale.
#
# Two rather than one, and the reason is fiscal calendars. An annual report
# filed in February 2026 covers 2025 and says so throughout; a quarterly report
# filed in July 2026 compares against the same quarter of 2025. A gap of one
# year is the normal register of a financial document, so flagging it would
# flag nearly every filing Loom holds. A gap of two means the newest thing the
# quote mentions is older than the last complete reporting year, which a
# document published now has to be reaching backwards to do.
STALE_YEARS_BEHIND = 2

# What a finding whose evidence is years old is still worth in a verdict about
# today. Not zero: an unresolved tax assessment from 2012 is still unresolved,
# and a company still carrying a 2022 subpoena is still carrying it. But a
# reader weighing it against last quarter's results is weighing two different
# periods, and the older one should not count as if it were current.
#
# Detection alone would have been the narrower change. It is not enough on its
# own: the stance is arithmetic over findings, so a finding flagged as stale in
# the interface while counting at full weight in the verdict produces a page
# that warns about evidence its own headline was computed from.
STALE_DISCOUNT = 0.4

# How far apart the two sides of a case have to be before the imbalance is
# worth reporting. A quarter's difference is ordinary, because filings and
# calls do not arrive on the same schedule. Half a year means one side is
# supported by a reporting period the other side has already moved past.
SIDE_AGE_GAP_DAYS = 180

# Both sides need at least this many findings before their ages are compared.
# A median of one observation is that observation, and calling two findings a
# stale comparison because one is older is noise.
MIN_PER_SIDE = 2

# Years outside this band are not periods, they are quantities: a dollar figure,
# a unit count, a patent number. 1990 is early enough to cover any filing Loom
# would read and late enough to exclude most of them.
_EARLIEST_YEAR = 1990
_LATEST_YEAR = 2100

_YEAR = re.compile(r"\b(19|20)\d{2}\b")


def _aware(value: datetime) -> datetime:
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def quoted_years(text: Optional[str]) -> set[int]:
    """Every calendar year named in a piece of text.

    Only four-digit years on word boundaries, which is what a filing uses to
    name a period. Deliberately not parsing "last quarter" or "the prior year":
    those resolve against the document's own date and are therefore already
    current, and guessing at them would manufacture warnings from ordinary
    prose.
    """
    if not text:
        return set()
    years = {int(match.group()) for match in _YEAR.finditer(text)}
    return {year for year in years if _EARLIEST_YEAR <= year <= _LATEST_YEAR}


@dataclass(frozen=True)
class Staleness:
    """What is old about one finding, if anything."""

    # The newest period the quote names, where it names one.
    newest_quoted_year: Optional[int] = None
    # The year the finding itself is dated to.
    finding_year: Optional[int] = None

    @property
    def years_behind(self) -> Optional[int]:
        if self.newest_quoted_year is None or self.finding_year is None:
            return None
        return self.finding_year - self.newest_quoted_year

    @property
    def is_stale(self) -> bool:
        behind = self.years_behind
        return behind is not None and behind >= STALE_YEARS_BEHIND

    @property
    def note(self) -> Optional[str]:
        """What to tell a reader, in a clause that fits beside the finding.

        None when there is nothing to say, which is most of the time. A
        staleness warning is only worth anything if it is rare.
        """
        if not self.is_stale:
            return None
        return (
            f"The evidence for this describes {self.newest_quoted_year}, "
            f"not the period the filing covers."
        )


def assess(signal) -> Staleness:
    """Whether a finding's own evidence is about an older period than its date.

    Reads the verbatim quote rather than the summary. The summary is Loom's
    sentence and may not carry the period at all; the quote is the company's,
    and it is the thing a reader is being shown as the receipt.
    """
    occurred = getattr(signal, "occurred_at", None)
    if occurred is None:
        return Staleness()
    years = quoted_years(getattr(signal, "evidence_quote", None))
    if not years:
        return Staleness(finding_year=_aware(occurred).year)
    return Staleness(
        newest_quoted_year=max(years),
        finding_year=_aware(occurred).year,
    )


def stale_ids(signals: list) -> set[str]:
    """The findings whose evidence is about an older period than their date."""
    return {
        str(getattr(signal, "id", id(signal)))
        for signal in signals
        if assess(signal).is_stale
    }


@dataclass(frozen=True)
class SideAges:
    """How old each side of a case is, and whether that is worth saying."""

    positive_days: Optional[int] = None
    negative_days: Optional[int] = None

    @property
    def gap_days(self) -> Optional[int]:
        if self.positive_days is None or self.negative_days is None:
            return None
        return abs(self.positive_days - self.negative_days)

    @property
    def is_imbalanced(self) -> bool:
        gap = self.gap_days
        return gap is not None and gap >= SIDE_AGE_GAP_DAYS

    @property
    def older_side(self) -> Optional[str]:
        if not self.is_imbalanced:
            return None
        return "positive" if self.positive_days > self.negative_days else "negative"

    @property
    def note(self) -> Optional[str]:
        """The imbalance in words, naming which side is the old one.

        Which side matters more than the size of the gap. "The case for is
        older than the case against" tells a reader what to discount; "the two
        sides differ by 240 days" tells them a number and leaves the work
        undone.
        """
        if not self.is_imbalanced:
            return None
        older = self.older_side
        months = round(max(self.positive_days, self.negative_days) / 30)
        if older == "positive":
            return (
                f"The encouraging evidence here is about {months} months older than the "
                f"concerns. A case whose two sides are not the same age is a comparison "
                f"across time rather than a balance of views."
            )
        return (
            f"The concerns here are about {months} months older than the encouraging "
            f"evidence. A case whose two sides are not the same age is a comparison "
            f"across time rather than a balance of views."
        )


def effective_age_days(signal, *, now: Optional[datetime] = None) -> Optional[int]:
    """How old a finding's evidence actually is, rather than how old the
    document that carried it is.

    The later of two ages: the document's own, and the age implied by the
    newest period the quote names. This is the distinction the whole module
    turns on. Measuring age by document date produced medians that were
    identical on both sides of every company in the corpus, because findings
    inherit the date of the filing they were extracted from and both sides of a
    case usually come from the same filing. Under that definition the check
    could never fire, which is why it found nothing.

    A quote naming 2023 in a filing published in 2026 is three-year-old
    evidence however recently it was restated, and a reader weighing it against
    something from last quarter is comparing across time.
    """
    now = now or datetime.now(timezone.utc)
    occurred = getattr(signal, "occurred_at", None)
    if occurred is None:
        return None
    by_document = (now - _aware(occurred)).days

    years = quoted_years(getattr(signal, "evidence_quote", None))
    if not years:
        return by_document
    # The end of the newest year named, which is the most recent moment the
    # quote could be describing. Taking the start of it would add a year of
    # staleness to every ordinary filing.
    quoted_end = datetime(max(years), 12, 31, tzinfo=timezone.utc)
    by_content = (now - quoted_end).days
    return max(by_document, by_content)


def side_ages(signals: list, *, now: Optional[datetime] = None) -> SideAges:
    """The median age of each side of the evidence.

    Median rather than mean: one very old finding on an otherwise current side
    should not make the side look stale, and one current finding should not
    rescue a side that is otherwise a year out of date.
    """
    now = now or datetime.now(timezone.utc)
    ages: dict[str, list[int]] = {"positive": [], "negative": []}
    for signal in signals:
        direction = getattr(signal, "market_direction", None)
        if direction not in ages:
            continue
        age = effective_age_days(signal, now=now)
        if age is None:
            continue
        ages[direction].append(age)

    def summarise(values: list[int]) -> Optional[int]:
        return int(median(values)) if len(values) >= MIN_PER_SIDE else None

    return SideAges(
        positive_days=summarise(ages["positive"]),
        negative_days=summarise(ages["negative"]),
    )


__all__ = [
    "MIN_PER_SIDE",
    "SIDE_AGE_GAP_DAYS",
    "STALE_DISCOUNT",
    "STALE_YEARS_BEHIND",
    "SideAges",
    "Staleness",
    "assess",
    "effective_age_days",
    "quoted_years",
    "side_ages",
    "stale_ids",
]
