"""Filings about a company, written by someone else.

Loom's document corpus has always been self-reported: a company's own filings,
its own transcripts, news written about it. This adapter adds the one category
none of those cover, which is what the rest of an industry is telling SEC that
happens to involve this company. A contract manufacturer describing order
volumes, a customer announcing a vendor decision, a competitor characterising
the same market.

**These are not the company's own words, and the distinction is load-bearing.**
A risk described in CoreWeave's 8-K is CoreWeave's view of its relationship
with NVIDIA, not NVIDIA's disclosure about itself, and an engine that conflated
the two would attribute a supplier's pessimism to the company as if it had said
it. The filer is recorded in the title, the subtype and the metadata so that
nothing downstream can mistake one for the other.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import httpx

from app.config import settings
from app.ingestion.base import DocumentSourceAdapter, RawDocumentDTO
from app.ingestion.edgar_fts import EdgarFullTextSearch
from app.ingestion.rate_limit import limiter
from app.ingestion.sec_edgar import SecEdgarAdapter
from app.services.company_lookup import get_company_lookup_service

logger = logging.getLogger(__name__)

# Marks a document as third-party. Everything downstream keys on this to avoid
# reading another company's filing as this company's statement.
DOC_SUBTYPE = "peer_mention"

# How far back to look, and how many filers to take. Bounded because this
# fetches a document per hit, and breadth of who is talking matters more than
# volume from any one of them.
DEFAULT_DAYS_BACK = 30
MAX_FILERS = 8

# Below this the "document" is a cover page or an exhibit stub rather than
# anything worth analysing.
_MIN_TEXT_CHARS = 1_000
_MAX_TEXT_CHARS = 40_000


class EcosystemMentionsAdapter(DocumentSourceAdapter):
    """Other companies' filings that name the tracked company."""

    source_name = "sec-edgar-fts"
    # Genuinely an SEC filing; the subtype carries that it is someone else's.
    source_type = "sec_edgar_filing"

    def __init__(self, days_back: int = DEFAULT_DAYS_BACK, max_filers: int = MAX_FILERS):
        self.days_back = days_back
        self.max_filers = max_filers
        self._search = EdgarFullTextSearch()
        self._extractor = SecEdgarAdapter()
        self._client = httpx.Client(
            headers={"User-Agent": settings.sec_edgar_user_agent},
            timeout=30.0,
            follow_redirects=True,
        )

    def fetch(self, ticker: str, since: Optional[datetime] = None) -> list[RawDocumentDTO]:
        info = get_company_lookup_service().lookup(ticker)
        if info is None:
            return []

        days_back = self.days_back
        if since is not None:
            # Respect an incremental cutoff, but never widen past the default:
            # a company with no stored documents would otherwise request years
            # of third-party filings on its first run.
            elapsed = (datetime.now(timezone.utc) - since).days
            days_back = max(1, min(self.days_back, elapsed))

        hits = self._search.mentions_of(
            info.name,
            exclude_cik=info.cik,
            days_back=days_back,
            limit=self.max_filers,
        )
        if not hits:
            return []

        documents: list[RawDocumentDTO] = []
        for hit in hits:
            text = self._document_text(hit.url)
            if len(text) < _MIN_TEXT_CHARS:
                continue

            published = (
                datetime.combine(hit.file_date, datetime.min.time(), tzinfo=timezone.utc)
                if hit.file_date
                else datetime.now(timezone.utc)
            )
            documents.append(
                RawDocumentDTO(
                    company_ticker=ticker,
                    source_type=self.source_type,
                    source_name=self.source_name,
                    source_url=hit.index_url,
                    doc_subtype=DOC_SUBTYPE,
                    # The filer leads the title so that a reader scanning a
                    # timeline never mistakes this for the company's own filing.
                    title=f"{hit.company_name} {hit.form} mentioning {ticker}",
                    published_at=published,
                    raw_text=text,
                    metadata={
                        "filed_by_cik": hit.cik,
                        "filed_by_name": hit.company_name,
                        "about_ticker": ticker,
                        "accession": hit.accession,
                        "form": hit.form,
                        "third_party": True,
                    },
                )
            )
        return documents

    def _document_text(self, url: str) -> str:
        try:
            limiter.acquire(url)
            response = self._client.get(url)
            if response.status_code != 200:
                return ""
            return self._extractor._extract_text(response.text)[:_MAX_TEXT_CHARS]
        except Exception:
            logger.debug("Could not fetch third-party filing %s", url, exc_info=True)
            return ""
