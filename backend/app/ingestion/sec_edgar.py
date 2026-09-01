"""SEC EDGAR adapter, the first concrete DocumentSourceAdapter.

Official, free, no API key: SEC only requires an honest, identifying
User-Agent header (see app.config.settings.sec_edgar_user_agent). Rate
limit is a self-imposed ~5 req/sec, comfortably under SEC's ~10 req/sec
fair-access guidance.

Ticker -> CIK resolution is delegated to CompanyLookupService (see
app/services/company_lookup.py) rather than duplicated here, that service
is also what lets "add ticker" resolve arbitrary tickers on the fly, so
both consumers share one cached lookup instead of each fetching SEC's
ticker directory independently.

The remaining call, `submissions/CIK{cik}.json`, returns a company's
recent filings (form type, filing date, accession number, primary
document filename); the primary document for each is then fetched from
www.sec.gov/Archives/edgar/... and its text extracted.

**8-K exhibits.** For an 8-K the primary document is only a cover sheet: it
names the item numbers and then says "see Exhibit 99.1". The actual earnings
press release, with the revenue, margin, and EPS figures and management's
quotes, is a separate file in the same accession. Measured on Apple's
2026-07-30 8-K, the primary document extracted to 3,475 characters of
boilerplate while Exhibit 99.1 held 10,463 characters of results; analysing
the cover sheet alone produced a signal that correctly but uselessly reported
the filing as "purely administrative" at 17% confidence.

So for 8-Ks the accession's exhibit list is read and EX-99* exhibits are
appended to the document text. This is also why Loom does not need a separate
press-release scraper: the authoritative copy of the release is already inside
the 8-K that announces it, from an official keyless source, with no per-company
HTML to break, and cross-referencing the two is free because they are the same
accession.
"""

import html
import logging
import re
from datetime import datetime, timezone

import httpx
from selectolax.parser import HTMLParser

from app.config import settings
from app.ingestion.base import DocumentSourceAdapter, RawDocumentDTO
from app.ingestion.rate_limit import limiter
from app.services.company_lookup import get_company_lookup_service

logger = logging.getLogger(__name__)

_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

# The ceiling itself now lives in ingestion/rate_limit.py, because it has to
# hold across every adapter and every worker thread at once rather than
# per call site.

# Phase 1 form types. Phase 5 adds 'DEF 14A' (proxy statements) here too,
# still a text document, just a new doc_subtype, no new adapter needed.
DEFAULT_FORM_TYPES = {"10-K", "10-Q", "8-K"}

# Forms whose primary document is a cover sheet rather than the disclosure.
# 10-K and 10-Q are deliberately excluded: their primary document *is* the
# filing, and their EX-99s are incidental.
EXHIBIT_BEARING_FORMS = {"8-K"}

# EX-99 is the "additional exhibits" class: press releases, earnings
# supplements, investor presentations. The `\.?\d` guard is what keeps
# EX-101.INS and friends (XBRL taxonomy plumbing) out.
_CONTENT_EXHIBIT_RE = re.compile(r"^EX-99(\.\d+)?$", re.IGNORECASE)

# One filing's exhibits should never dwarf a 10-K. Generous enough for any
# real earnings release, bounded enough that an attached merger agreement
# cannot turn one 8-K into the most expensive document in the batch.
_MAX_EXHIBIT_CHARS = 150_000

_DOCUMENT_ENTRY_RE = re.compile(
    r"<TYPE>([^\r\n<]+)\s*<SEQUENCE>([^\r\n<]+)\s*<FILENAME>([^\r\n<]+)"
)
_ITEMS_RE = re.compile(r"^<ITEMS>([\d.]+)", re.MULTILINE)


class SecEdgarAdapter(DocumentSourceAdapter):
    source_name = "sec-edgar"
    source_type = "sec_edgar_filing"

    def __init__(self, form_types: set[str] | None = None):
        self.form_types = form_types or DEFAULT_FORM_TYPES
        self._client = httpx.Client(
            headers={"User-Agent": settings.sec_edgar_user_agent},
            timeout=30.0,
        )

    def fetch(self, ticker: str, since: datetime | None = None) -> list[RawDocumentDTO]:
        info = get_company_lookup_service().lookup(ticker)
        if info is None:
            logger.warning("SEC EDGAR: no CIK found for ticker %s", ticker)
            return []
        cik10 = info.cik

        submissions = self._fetch_submissions(cik10)
        if submissions is None:
            return []

        documents: list[RawDocumentDTO] = []
        recent = submissions.get("filings", {}).get("recent", {})
        forms = recent.get("form", [])
        dates = recent.get("filingDate", [])
        accessions = recent.get("accessionNumber", [])
        primary_docs = recent.get("primaryDocument", [])

        for form, filing_date, accession, primary_doc in zip(
            forms, dates, accessions, primary_docs, strict=False
        ):
            if form not in self.form_types:
                continue
            published_at = datetime.strptime(filing_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if since is not None and published_at < since:
                continue

            try:
                dto = self._fetch_filing_document(
                    ticker=ticker,
                    cik10=cik10,
                    form=form,
                    accession=accession,
                    primary_doc=primary_doc,
                    published_at=published_at,
                )
            except Exception:
                # One broken filing should never take down the rest of the batch.
                logger.exception(
                    "SEC EDGAR: failed to fetch %s filing %s for %s", form, accession, ticker
                )
                continue

            if dto is not None:
                documents.append(dto)

        return documents

    def _fetch_submissions(self, cik10: str) -> dict | None:
        url = _SUBMISSIONS_URL.format(cik10=cik10)
        limiter.acquire(url)
        resp = self._client.get(url)
        if resp.status_code != 200:
            logger.warning("SEC EDGAR: submissions fetch failed (%s) for CIK %s", resp.status_code, cik10)
            return None
        return resp.json()

    def _fetch_filing_document(
        self,
        *,
        ticker: str,
        cik10: str,
        form: str,
        accession: str,
        primary_doc: str,
        published_at: datetime,
    ) -> RawDocumentDTO | None:
        if not primary_doc:
            return None
        accession_nodash = accession.replace("-", "")
        cik_no_leading_zeros = str(int(cik10))
        url = f"{_ARCHIVES_BASE}/{cik_no_leading_zeros}/{accession_nodash}/{primary_doc}"

        limiter.acquire(url)
        resp = self._client.get(url)
        if resp.status_code != 200:
            logger.warning("SEC EDGAR: document fetch failed (%s) for %s", resp.status_code, url)
            return None

        raw_text = self._extract_text(resp.text)
        if not raw_text.strip():
            return None

        metadata = {"accession_number": accession, "cik": cik10}

        if form in EXHIBIT_BEARING_FORMS:
            exhibit_text, exhibit_names, items = self._fetch_exhibits(
                cik10=cik10, accession=accession, primary_doc=primary_doc
            )
            if exhibit_text:
                raw_text = f"{raw_text}\n\n{exhibit_text}"
            if exhibit_names:
                metadata["exhibits"] = exhibit_names
            if items:
                metadata["items"] = items

        return RawDocumentDTO(
            company_ticker=ticker,
            source_type=self.source_type,
            source_name=self.source_name,
            source_url=url,
            doc_subtype=form,
            title=f"{ticker} {form} filed {published_at.date().isoformat()}",
            published_at=published_at,
            raw_text=raw_text,
            metadata=metadata,
        )

    def _fetch_exhibits(
        self, *, cik10: str, accession: str, primary_doc: str
    ) -> tuple[str, list[str], list[str]]:
        """Return (combined_exhibit_text, exhibit_types, item_numbers).

        Always returns rather than raises: an 8-K whose exhibits cannot be read
        is still worth storing as its cover sheet, which is strictly what was
        stored before this existed.
        """
        accession_nodash = accession.replace("-", "")
        cik_no_leading_zeros = str(int(cik10))
        base = f"{_ARCHIVES_BASE}/{cik_no_leading_zeros}/{accession_nodash}"

        try:
            index_url = f"{base}/{accession}-index-headers.html"
            limiter.acquire(index_url)
            resp = self._client.get(index_url)
            if resp.status_code != 200:
                logger.info("SEC EDGAR: no exhibit index (%s) for %s", resp.status_code, accession)
                return ("", [], [])
            header = html.unescape(resp.text)
        except Exception:
            logger.warning("SEC EDGAR: exhibit index fetch failed for %s", accession, exc_info=True)
            return ("", [], [])

        items = _ITEMS_RE.findall(header)

        parts: list[str] = []
        names: list[str] = []
        budget = _MAX_EXHIBIT_CHARS
        for exhibit_type, _sequence, filename in _DOCUMENT_ENTRY_RE.findall(header):
            exhibit_type = exhibit_type.strip()
            filename = filename.strip()
            if filename == primary_doc or not _CONTENT_EXHIBIT_RE.match(exhibit_type):
                continue
            if budget <= 0:
                logger.info("SEC EDGAR: exhibit budget exhausted for %s", accession)
                break

            text = self._fetch_exhibit_text(f"{base}/{filename}")
            if not text:
                continue
            text = text[:budget]
            budget -= len(text)
            parts.append(f"=== Exhibit {exhibit_type} ===\n{text}")
            names.append(exhibit_type)

        return ("\n\n".join(parts), names, items)

    def _fetch_exhibit_text(self, url: str) -> str:
        try:
            limiter.acquire(url)
            resp = self._client.get(url)
        except Exception:
            logger.warning("SEC EDGAR: exhibit fetch failed for %s", url, exc_info=True)
            return ""
        if resp.status_code != 200:
            logger.info("SEC EDGAR: exhibit fetch returned %s for %s", resp.status_code, url)
            return ""
        return self._extract_text(resp.text).strip()

    # Modern SEC filings are inline-XBRL (iXBRL): the raw HTML embeds a
    # hidden block of machine-readable XBRL facts/tags (`<ix:header>`,
    # anything under `display:none`) that naive text extraction would
    # otherwise dump verbatim ahead of the actual readable filing text.
    _NOISE_SELECTOR = 'script, style, ix\\:header, [style*="display:none"], [style*="display: none"]'

    @classmethod
    def _extract_text(cls, html: str) -> str:
        tree = HTMLParser(html)
        if tree.body is None:
            return ""
        for node in tree.css(cls._NOISE_SELECTOR):
            node.decompose()
        return tree.body.text(separator="\n", strip=True)
