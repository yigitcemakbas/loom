"""EDGAR's live filing feed: how Loom finds out that something happened.

The rest of the ingestion layer asks, per company, "what is new since I last
looked". That is the right shape for building a corpus and the wrong shape for
reacting: with hundreds of companies it costs hundreds of requests per sweep,
so the sweep has to be infrequent, so the news is old by the time it lands.
Loom's scheduled refresh runs every six hours, which is fine for a brief and
useless for a reaction.

This module inverts it. EDGAR publishes one feed of every filing it has just
accepted, across all filers, ordered by acceptance time. One request returns
everything that happened in the last few minutes anywhere in the market, and
the universe filter is applied locally against CIKs already in the database.
Cost is therefore constant in the size of the universe rather than linear,
which is what makes a short poll interval affordable: a thousand companies
costs the same one request as eleven.

Acceptance time is the timestamp that matters here, not the filing date. A
filing dated today may have been accepted at 17:30, and the difference between
those two facts is the entire opportunity.
"""

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

import httpx

from app.config import settings
from app.ingestion.rate_limit import limiter

logger = logging.getLogger(__name__)

_FEED_URL = "https://www.sec.gov/cgi-bin/browse-edgar"
_ATOM = {"a": "http://www.w3.org/2005/Atom"}

# EDGAR caps this well below what it claims; 100 is reliably honoured and is
# far more than a short poll interval can produce.
MAX_ENTRIES = 100

# "8-K - COMPANY NAME (0001234567) (Filer)"
# The form pattern must allow hyphens, since every form type worth watching
# contains one, and separate on the first " - " rather than the first "-".
_TITLE_RE = re.compile(r"^\s*(?P<form>.+?)\s+-\s+(?P<name>.*?)\s+\((?P<cik>\d{10})\)")

# Forms worth waking up for. Deliberately narrow: this feed carries the entire
# market's paperwork, the large majority of which is ownership housekeeping
# that moves nothing.
DEFAULT_FORM_TYPES = ("8-K", "10-Q", "10-K")


@dataclass(frozen=True)
class FilingNotice:
    """One filing EDGAR has just accepted."""

    cik: str                 # zero-padded to 10, matching companies.cik
    company_name: str
    form: str
    accession: str
    index_url: str
    accepted_at: Optional[datetime]

    @property
    def is_amendment(self) -> bool:
        return self.form.upper().endswith("/A")


def _parse_entry(entry) -> Optional[FilingNotice]:
    title = (entry.findtext("a:title", default="", namespaces=_ATOM) or "").strip()
    match = _TITLE_RE.match(title)
    if not match:
        return None

    link_el = entry.find("a:link", _ATOM)
    index_url = link_el.get("href") if link_el is not None else ""
    if not index_url:
        return None
    if index_url.startswith("/"):
        index_url = f"https://www.sec.gov{index_url}"

    # The accession number is the filename stem of the index page.
    accession = index_url.rsplit("/", 1)[-1].replace("-index.htm", "").replace(".txt", "")

    accepted_at = None
    raw = entry.findtext("a:updated", default="", namespaces=_ATOM)
    if raw:
        try:
            accepted_at = datetime.fromisoformat(raw)
        except ValueError:
            logger.debug("Unparseable acceptance timestamp %r", raw)

    return FilingNotice(
        cik=match.group("cik"),
        company_name=match.group("name").strip(),
        form=match.group("form").strip(),
        accession=accession,
        index_url=index_url,
        accepted_at=accepted_at,
    )


class SecRealtimeFeed:
    """Polls EDGAR's accepted-filings feed.

    Holds its own client rather than sharing one with the batch adapters, so a
    slow backfill cannot occupy the connection the watcher depends on. Requests
    still pass through the shared limiter, because SEC's fair-access ceiling
    applies to this process as a whole no matter which code path is asking.
    """

    def __init__(self, form_types: Iterable[str] = DEFAULT_FORM_TYPES):
        self.form_types = tuple(form_types)
        self._client = httpx.Client(
            headers={"User-Agent": settings.sec_edgar_user_agent},
            timeout=15.0,
            follow_redirects=True,
        )

    def fetch(self, form_type: Optional[str] = None, count: int = MAX_ENTRIES) -> list[FilingNotice]:
        """Everything EDGAR has accepted recently, newest first.

        Never raises. The watcher that calls this runs unattended on a short
        interval, and a transient failure must cost one cycle rather than the
        loop.
        """
        params = {
            "action": "getcurrent",
            "company": "",
            "dateb": "",
            "owner": "include",
            "count": str(count),
            "output": "atom",
        }
        if form_type:
            params["type"] = form_type

        try:
            limiter.acquire(_FEED_URL)
            response = self._client.get(_FEED_URL, params=params)
        except Exception:
            logger.warning("EDGAR live feed request failed.", exc_info=True)
            return []

        if response.status_code != 200:
            logger.warning("EDGAR live feed returned %s.", response.status_code)
            return []

        try:
            root = ET.fromstring(response.content)
        except ET.ParseError:
            logger.warning("EDGAR live feed returned unparseable XML.")
            return []

        notices = []
        for entry in root.findall("a:entry", _ATOM):
            notice = _parse_entry(entry)
            if notice is not None:
                notices.append(notice)
        return notices

    def fetch_all_forms(self, count: int = MAX_ENTRIES) -> list[FilingNotice]:
        """One pass over every form type being watched, deduplicated.

        Queried per form rather than unfiltered because an unfiltered feed is
        dominated by Form 4 and ownership filings, and the handful of entries
        that matter fall off the end of the window between polls.
        """
        seen: set[str] = set()
        out: list[FilingNotice] = []
        for form in self.form_types:
            for notice in self.fetch(form_type=form, count=count):
                if notice.accession in seen:
                    continue
                seen.add(notice.accession)
                out.append(notice)

        out.sort(key=lambda n: n.accepted_at or datetime.min, reverse=True)
        return out
