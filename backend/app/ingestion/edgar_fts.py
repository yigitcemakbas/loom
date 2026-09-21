"""EDGAR full-text search: what everyone else is saying about a company.

Every other source in this project answers "what has this company published".
This one answers the question no other source can: what have *other* companies
disclosed that involves it. A supplier warning about demand, a customer
announcing a switch of vendor, a competitor describing the same market, all of
it is filed with SEC and none of it appears anywhere in a company's own
paperwork.

**Form filtering is not tuning, it is the difference between a source and a
firehose.** An unfiltered search for a large company returns ten thousand
matches that are almost entirely portfolio disclosures: every ETF and fund
holding the stock lists it in NPORT-P, 13F-HR and N-CSR filings. Measured on
NVIDIA over seven weeks, the unfiltered query returned 10,000+ hits of which
the visible majority were fund holdings; restricted to operating-company forms
it returned 145, and the filers were CoreWeave, Sanmina, IREN and HIVE, which
are respectively its largest customer, a contract manufacturer, and two compute
operators. Same query, same window, entirely different source.
"""

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

import httpx

from app.config import settings
from app.ingestion.rate_limit import limiter

logger = logging.getLogger(__name__)

_SEARCH_URL = "https://efts.sec.gov/LATEST/search-index"
_ARCHIVES = "https://www.sec.gov/Archives/edgar/data"

# Forms an operating company files about its own business. Everything omitted
# here is a holdings or administrative filing, which is where the noise lives.
OPERATING_FORMS = ("8-K", "10-K", "10-Q", "S-1", "DEF 14A", "20-F")

# EDGAR returns at most this many hits per request regardless of the total.
PAGE_SIZE = 100

# Corporate suffixes that make a name match worse rather than better: almost
# no filing writes "NVIDIA Corporation" when it means NVIDIA.
# EDGAR labels a filer as "NAME  (TICKER)  (CIK 0000001234)".
_CIK_IN_LABEL = re.compile(r"CIK\s+(\d{10})")

_SUFFIXES = re.compile(
    r"\b(inc|inc\.|incorporated|corp|corp\.|corporation|co|co\.|company|"
    r"plc|ltd|ltd\.|limited|holdings|group|n\.v\.|s\.a\.|ag)\b\.?",
    re.IGNORECASE,
)


def core_name(company_name: str) -> str:
    """The part of a company's name worth searching for."""
    cleaned = _SUFFIXES.sub(" ", company_name or "")
    cleaned = re.sub(r"[,\.]", " ", cleaned)
    return " ".join(cleaned.split()).strip()


@dataclass(frozen=True)
class FilingHit:
    """One document matching a search."""

    accession: str
    document: str
    cik: str
    company_name: str
    form: str
    file_date: Optional[date]
    items: list[str]
    description: Optional[str]

    @property
    def url(self) -> str:
        return f"{_ARCHIVES}/{int(self.cik)}/{self.accession.replace('-', '')}/{self.document}"

    @property
    def index_url(self) -> str:
        return (
            f"{_ARCHIVES}/{int(self.cik)}/{self.accession.replace('-', '')}/"
            f"{self.accession}-index.htm"
        )


def _parse_hit(raw: dict) -> Optional[FilingHit]:
    source = raw.get("_source") or {}
    # "_id" is "{accession}:{filename}", which is the only place the exact
    # matching document is identified; the source block names the filing.
    identifier = raw.get("_id") or ""
    if ":" not in identifier:
        return None
    accession, document = identifier.split(":", 1)

    ciks = source.get("ciks") or []
    if not ciks:
        return None

    filed = None
    if source.get("file_date"):
        try:
            filed = datetime.strptime(source["file_date"], "%Y-%m-%d").date()
        except ValueError:
            pass

    names = source.get("display_names") or []
    forms = source.get("root_forms") or []

    return FilingHit(
        accession=accession,
        document=document,
        cik=str(ciks[0]).zfill(10),
        company_name=names[0] if names else "unknown",
        form=forms[0] if forms else (source.get("file_type") or "unknown"),
        file_date=filed,
        items=source.get("items") or [],
        description=source.get("file_description"),
    )


class EdgarFullTextSearch:
    """Searches inside the text of every filing SEC holds."""

    def __init__(self):
        self._client = httpx.Client(
            headers={"User-Agent": settings.sec_edgar_user_agent},
            timeout=30.0,
            follow_redirects=True,
        )

    def search(
        self,
        query: str,
        *,
        forms: Optional[Iterable[str]] = OPERATING_FORMS,
        since: Optional[date] = None,
        until: Optional[date] = None,
        ciks: Optional[Iterable[str]] = None,
    ) -> list[FilingHit]:
        """Filings whose text matches. Never raises.

        `query` is passed through as written, so a caller wanting a phrase must
        quote it. An unquoted multi-word query matches the words separately and
        returns far more, and far less relevant, results.
        """
        params: list[tuple[str, str]] = [("q", query)]
        if forms:
            params.append(("forms", ",".join(forms)))
        if since:
            params.append(("startdt", since.isoformat()))
            params.append(("enddt", (until or date.today()).isoformat()))
        if ciks:
            params.append(("ciks", ",".join(ciks)))

        try:
            limiter.acquire(_SEARCH_URL)
            response = self._client.get(_SEARCH_URL, params=params)
        except Exception:
            logger.warning("EDGAR full-text search failed for %r", query, exc_info=True)
            return []

        if response.status_code != 200:
            logger.info("EDGAR full-text search returned %s for %r", response.status_code, query)
            return []

        try:
            hits = (response.json().get("hits") or {}).get("hits") or []
        except Exception:
            logger.warning("EDGAR full-text search returned unparseable JSON.")
            return []

        parsed = [_parse_hit(h) for h in hits]
        return [h for h in parsed if h is not None]

    def filer_counts(
        self,
        query: str,
        *,
        forms: Optional[Iterable[str]] = OPERATING_FORMS,
        since: Optional[date] = None,
    ) -> dict[str, int]:
        """Which filers mention this term, and in how many filings, in one request.

        EDGAR returns an `entity_filter` aggregation alongside the hits, giving
        a per-filer document count across the whole result set rather than just
        the page. That is what makes a dependency graph affordable: the
        alternative is a request per candidate pair, which for a universe of a
        hundred and thirty companies is seventeen thousand requests instead of
        a hundred and thirty.

        Returns CIK, zero-padded, to the number of matching filings.
        """
        params: list[tuple[str, str]] = [("q", query)]
        if forms:
            params.append(("forms", ",".join(forms)))
        if since:
            params.append(("startdt", since.isoformat()))
            params.append(("enddt", date.today().isoformat()))

        try:
            limiter.acquire(_SEARCH_URL)
            response = self._client.get(_SEARCH_URL, params=params)
        except Exception:
            logger.warning("EDGAR filer counts failed for %r", query, exc_info=True)
            return {}

        if response.status_code != 200:
            return {}

        try:
            buckets = (
                (response.json().get("aggregations") or {})
                .get("entity_filter", {})
                .get("buckets", [])
            )
        except Exception:
            return {}

        counts: dict[str, int] = {}
        for bucket in buckets:
            # Bucket keys look like "AMD INC  (AMD)  (CIK 0000002488)".
            match = _CIK_IN_LABEL.search(bucket.get("key") or "")
            doc_count = bucket.get("doc_count")
            if match and doc_count:
                counts[match.group(1)] = int(doc_count)
        return counts

    def mentions_of(
        self,
        company_name: str,
        *,
        exclude_cik: Optional[str] = None,
        days_back: int = 30,
        limit: int = 20,
    ) -> list[FilingHit]:
        """Filings by *other* companies that name this one.

        The exclusion is done here rather than in the query because EDGAR has
        no "not this filer" parameter. Deduplicated by filer so that one
        company filing several documents in a window does not crowd out the
        rest: the point is breadth of who is talking, not volume.
        """
        name = core_name(company_name)
        if not name:
            return []

        since = date.today() - timedelta(days=days_back)
        hits = self.search(f'"{name}"', since=since)

        excluded = str(exclude_cik).zfill(10) if exclude_cik else None
        seen_filers: set[str] = set()
        out: list[FilingHit] = []
        for hit in hits:
            if excluded and hit.cik == excluded:
                continue
            if hit.cik in seen_filers:
                continue
            seen_filers.add(hit.cik)
            out.append(hit)
            if len(out) >= limit:
                break
        return out
