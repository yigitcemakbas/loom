"""Prompt and schema for pulling the headline numbers out of an earnings release.

This is the one place the fast path is allowed to call a model, and the reason
is that the job is semantic rather than textual. Three things in a real release
defeat pattern matching, and all three fail silently rather than loudly:

  * GAAP and non-GAAP figures sit side by side, often in the same sentence
    ("GAAP and non-GAAP earnings per diluted share were ..."), and consensus
    estimates are quoted on the non-GAAP basis. Picking the wrong one does not
    error, it produces a confident and wrong surprise percentage.
  * The numbers usually live in a table with three columns, this quarter, last
    quarter and the year-ago quarter, with nothing in the text marking which is
    which.
  * Sentence boundaries are unusable, because every figure contains a period.

A wrong number here is worse than no number, since it would feed a surprise
calculation that a reader might act on. The schema therefore permits nulls
everywhere and the prompt is explicit that declining to answer is correct when
the release does not plainly say.
"""

from pydantic import BaseModel, Field

PROMPT_VERSION = "2026-09-21.1"


class EarningsFigures(BaseModel):
    eps_gaap: float | None = Field(
        default=None,
        description=(
            "GAAP diluted earnings per share for the THREE MONTHS just ended. "
            "Not a six, nine or twelve month cumulative figure."
        ),
    )
    eps_adjusted: float | None = Field(
        default=None,
        description="Non-GAAP or adjusted diluted earnings per share for the quarter just ended, if reported.",
    )
    revenue: float | None = Field(
        default=None,
        description=(
            "Total revenue for the THREE MONTHS just ended, in whole dollars "
            "(e.g. 57000000000 for $57.0 billion). Not a cumulative figure."
        ),
    )
    period_label: str | None = Field(
        default=None, description="The quarter being reported, e.g. 'Q2 2026'."
    )
    guidance_summary: str | None = Field(
        default=None,
        description="Any forward guidance given, in one sentence. Null if none.",
    )
    confident: bool = Field(
        description="True only if the figures were stated plainly. False if they had to be inferred."
    )


SYSTEM = """You extract the headline figures from a company's earnings release.

Report ONLY the three months just ended.

Releases put several periods side by side in the same table: the prior quarter,
the year-ago quarter, and very often a cumulative "Six Months Ended", "Nine
Months Ended" or "Twelve Months Ended" column. A cumulative figure is several
times larger than the quarter and is the single most common way this extraction
goes wrong. If you cannot tell which column is the three months just ended,
return null rather than the largest number you can find.

GAAP and non-GAAP (also called adjusted) figures are different numbers and must
not be merged. Fill in whichever are present and leave the other null.

Revenue must be in whole dollars. "$57.0 billion" is 57000000000.

If a figure is not plainly stated, leave it null and set confident to false.
Returning nothing is correct and useful. Guessing is not: a wrong number here
feeds a calculation someone may act on, and it will not look wrong.
"""
