"""Pull the headline numbers out of an earnings release.

Invoked only when a filing carries 8-K item 2.02 (Results of Operations), which
is roughly four events per company per year, so the cost of the single model
call this makes is negligible even across a large watched universe.

It is nonetheless a deliberate exception to the rule that the reaction path
does not call a model, and worth naming as one. The justification is the
window: an earnings reaction plays out over minutes, not the seconds that a
filing keyword match has to fit inside, and one paced call fits inside minutes.
If that ever stops being true, the honest fix is to drop the surprise figure
rather than to guess it from a pattern.
"""

import logging
import re
from typing import Optional

from app.engine.llm_client import LLMClient, LLMUnavailableError, get_llm_client
from app.engine.prompts import earnings_release
from app.engine.prompts.earnings_release import EarningsFigures

logger = logging.getLogger(__name__)

# The item number that means "we are reporting results".
RESULTS_ITEM = "2.02"

# Releases put the numbers near the top; the back half is reconciliation
# tables and legal boilerplate. Trimming keeps the call small and fast.
MAX_RELEASE_CHARS = 18_000

# A sanity bound. Real quarterly diluted EPS outside this range is close enough
# to unheard of that a value beyond it is far more likely to be a parsing
# artefact, a cumulative figure, or a number in cents.
_EPS_PLAUSIBLE = (-100.0, 100.0)

# Below a billionth of a dollar of revenue something has gone wrong with units.
_MIN_REVENUE = 1_000_000.0


def is_results_filing(item_numbers: list[str]) -> bool:
    """Whether this filing is an earnings release."""
    return any(item.strip() == RESULTS_ITEM for item in item_numbers or [])


def looks_like_earnings(text: str) -> bool:
    """Cheap pre-filter before spending a call.

    Some item 2.02 filings attach only a bare exhibit reference, and calling a
    model to read a cover sheet wastes quota for a guaranteed null.
    """
    if not text:
        return False
    return bool(
        re.search(r"earnings per|diluted eps|\bEPS\b|net revenue|total revenue", text, re.I)
    )


def _plausible_eps(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    low, high = _EPS_PLAUSIBLE
    if not low <= value <= high:
        logger.info("Discarding implausible EPS %.4f from an earnings release.", value)
        return None
    return value


def _plausible_revenue(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    if value < _MIN_REVENUE:
        # Almost always a figure reported in millions and handed over as-is.
        logger.info("Discarding implausible revenue %.0f from an earnings release.", value)
        return None
    return value


def extract_figures(text: str, client: Optional[LLMClient] = None) -> Optional[EarningsFigures]:
    """Read an earnings release. Returns None when nothing reliable was found.

    Never raises: this runs inside the watcher, and a release that cannot be
    parsed must still produce an assessment based on everything else the engine
    knows, rather than costing the whole event.
    """
    if not looks_like_earnings(text):
        return None

    try:
        client = client or get_llm_client()
        figures = client.parse(
            system=earnings_release.SYSTEM,
            user_content=text[:MAX_RELEASE_CHARS],
            schema=EarningsFigures,
        )
    except LLMUnavailableError:
        logger.info("Earnings figures unavailable: no model configured or quota exhausted.")
        return None
    except Exception:
        logger.warning("Earnings extraction failed.", exc_info=True)
        return None

    if figures is None:
        return None

    figures.eps_gaap = _plausible_eps(figures.eps_gaap)
    figures.eps_adjusted = _plausible_eps(figures.eps_adjusted)
    figures.revenue = _plausible_revenue(figures.revenue)

    if figures.eps_gaap is None and figures.eps_adjusted is None and figures.revenue is None:
        return None
    return figures


# A genuine quarterly EPS surprise beyond this is rare. A period or basis
# mismatch, on the other hand, produces one routinely, so a figure implying a
# surprise this large is far more likely to be wrong than remarkable.
MAX_CREDIBLE_SURPRISE_PERCENT = 25.0


def comparable_eps(figures: EarningsFigures) -> tuple[Optional[float], str]:
    """The EPS that can honestly be measured against consensus, and its basis.

    Only an adjusted figure is returned for comparison, and that restriction is
    the result of checking four real releases against the actuals a data
    provider reported. Where the company published a non-GAAP figure the match
    was exact. Where only GAAP was available it was wrong every time, because
    sell-side consensus is quoted on an adjusted basis: Apple's quarter
    genuinely reported $2.02 of GAAP diluted EPS against a $1.93 consensus, but
    the comparable actual was $1.91, so a naive comparison turns a small miss
    into a beat and inverts the direction of the trade.

    A GAAP figure is still worth extracting and showing. It is simply not
    comparable to the estimate, and pretending otherwise produces a confident
    number pointing the wrong way.
    """
    if figures.eps_adjusted is not None:
        return figures.eps_adjusted, "adjusted"
    if figures.eps_gaap is not None:
        # Reported for display, explicitly not for comparison.
        return None, "gaap_only_not_comparable"
    return None, "none"


def surprise_is_credible(actual: Optional[float], estimate: Optional[float]) -> bool:
    """Second line of defence behind the basis rule.

    Catches what the prompt and the basis check miss, which in testing was a
    release carrying a "Twelve Months Ended" column: the model took the
    cumulative figure, and $5.75 against a $1.86 estimate implied a two hundred
    percent beat. Every plausibility bound on the raw number passes that, since
    $5.75 is an ordinary EPS. Only the comparison reveals it.
    """
    if actual is None or not estimate:
        return False
    surprise = abs((actual - estimate) / abs(estimate) * 100)
    if surprise > MAX_CREDIBLE_SURPRISE_PERCENT:
        logger.warning(
            "Discarding an earnings surprise of %.0f%%: far more likely a period "
            "or basis mismatch than a real result.", surprise,
        )
        return False
    return True
