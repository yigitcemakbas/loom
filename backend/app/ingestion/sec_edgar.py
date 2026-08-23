"""SEC EDGAR adapter — the first concrete DocumentSourceAdapter.

Official, free, no API key: SEC only requires an honest, identifying
User-Agent header (see app.config.settings.sec_edgar_user_agent). Rate
limit is a self-imposed ~5 req/sec, comfortably under SEC's ~10 req/sec
fair-access guidance.

Two calls to data.sec.gov:
1. `company_tickers.json` — a static ticker -> CIK lookup table.
2. `submissions/CIK{cik}.json` — a company's recent filings (form type,
   filing date, accession number, primary document filename).

The primary document for each filing is then fetched from
www.sec.gov/Archives/edgar/... and its text extracted.
"""

import logging
import time
from datetime import datetime, timezone

import httpx
from selectolax.parser import HTMLParser

from app.config import settings
from app.ingestion.base import DocumentSourceAdapter, RawDocumentDTO

logger = logging.getLogger(__name__)

_TICKER_LOOKUP_URL = "https://www.sec.gov/files/company_tickers.json"
_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"

_RATE_LIMIT_SECONDS = 0.2  # ~5 req/sec, well under SEC's fair-access guidance

# Phase 1 form types. Phase 5 adds 'DEF 14A' (proxy statements) here too —
# still a text document, just a new doc_subtype, no new adapter needed.
DEFAULT_FORM_TYPES = {"10-K", "10-Q", "8-K"}


class SecEdgarAdapter(DocumentSourceAdapter):
    source_name = "sec-edgar"
    source_type = "sec_edgar_filing"

    def __init__(self, form_types: set[str] | None = None):
        self.form_types = form_types or DEFAULT_FORM_TYPES
        self._client = httpx.Client(
            headers={"User-Agent": settings.sec_edgar_user_agent},
            timeout=30.0,
        )
        self._cik_cache: dict[str, str] | None = None

    def fetch(self, ticker: str, since: datetime | None = None) -> list[RawDocumentDTO]:
        cik10 = self._lookup_cik(ticker)
        if cik10 is None:
            logger.warning("SEC EDGAR: no CIK found for ticker %s", ticker)
            return []

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

    def _lookup_cik(self, ticker: str) -> str | None:
        if self._cik_cache is None:
            resp = self._client.get(_TICKER_LOOKUP_URL)
            resp.raise_for_status()
            data = resp.json()
            # Payload shape: {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."}, ...}
            self._cik_cache = {
                entry["ticker"].upper(): str(entry["cik_str"]).zfill(10) for entry in data.values()
            }
            time.sleep(_RATE_LIMIT_SECONDS)
        return self._cik_cache.get(ticker.upper())

    def _fetch_submissions(self, cik10: str) -> dict | None:
        resp = self._client.get(_SUBMISSIONS_URL.format(cik10=cik10))
        time.sleep(_RATE_LIMIT_SECONDS)
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

        resp = self._client.get(url)
        time.sleep(_RATE_LIMIT_SECONDS)
        if resp.status_code != 200:
            logger.warning("SEC EDGAR: document fetch failed (%s) for %s", resp.status_code, url)
            return None

        raw_text = self._extract_text(resp.text)
        if not raw_text.strip():
            return None

        return RawDocumentDTO(
            company_ticker=ticker,
            source_type=self.source_type,
            source_name=self.source_name,
            source_url=url,
            doc_subtype=form,
            title=f"{ticker} {form} filed {published_at.date().isoformat()}",
            published_at=published_at,
            raw_text=raw_text,
            metadata={"accession_number": accession, "cik": cik10},
        )

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
