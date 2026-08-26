"""SEC Form 4 insider transactions, the project's first FactSourceAdapter.

Form 4 is filed within two business days of an insider trading their own
company's stock. The raw XML is well structured, so nothing here needs a
language model: the numbers arrive as numbers.

**Transaction codes are the whole story, and getting them wrong is the classic
error in insider data.** A Form 4 reporting that an officer "disposed of"
shares is usually not a decision to sell. Vesting restricted stock triggers
automatic share withholding to cover income tax (code F), and exercising
options (code M) shows up as an acquisition followed by a disposal. Both are
mechanical consequences of a compensation schedule set years earlier. Counting
them as insider selling is how "executives dumped stock!" stories get written
about companies where no executive decided anything.

Only codes S and P represent a discretionary open-market trade. Every
transaction is still stored, because the full record is what makes the data
auditable, but the code is recorded on each fact so the rules in
engine/fact_rules.py can weigh discretionary trades and ignore the plumbing.
"""

import hashlib
import logging
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import httpx

from app.config import settings
from app.ingestion.base import FactSourceAdapter, StructuredFactDTO
from app.services.company_lookup import get_company_lookup_service

logger = logging.getLogger(__name__)

_SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik10}.json"
_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
_RATE_LIMIT_SECONDS = 0.2

# A large company files hundreds of Form 4s. Without a bound, a first ingest
# would issue one request per filing before anything could be shown.
DEFAULT_MAX_FILINGS = 40

# Discretionary open-market trades. Everything else on a Form 4 is a
# consequence of a compensation plan rather than a decision to trade.
OPEN_MARKET_CODES = {"S", "P"}

_CODE_MEANINGS = {
    "P": "open-market purchase",
    "S": "open-market sale",
    "A": "grant or award",
    "M": "option exercise",
    "F": "shares withheld for taxes",
    "G": "gift",
    "C": "conversion",
    "X": "in-the-money option exercise",
}


class SecForm4Adapter(FactSourceAdapter):
    source_name = "sec-edgar-form4"
    source_type = "insider_transaction"

    def __init__(self, max_filings: int = DEFAULT_MAX_FILINGS):
        self.max_filings = max_filings
        self._client = httpx.Client(
            headers={"User-Agent": settings.sec_edgar_user_agent}, timeout=30.0
        )

    def fetch(self, ticker: str, since: datetime | None = None) -> list[StructuredFactDTO]:
        info = get_company_lookup_service().lookup(ticker)
        if info is None:
            logger.warning("Form 4: no CIK found for %s", ticker)
            return []

        filings = self._recent_form4_filings(info.cik, since=since)
        facts: list[StructuredFactDTO] = []
        for accession, primary_doc in filings[: self.max_filings]:
            try:
                facts.extend(self._parse_filing(ticker, info.cik, accession, primary_doc))
            except Exception:
                # One malformed filing must not cost the rest of the batch.
                logger.exception("Form 4: failed to parse %s for %s", accession, ticker)
        return facts

    def _recent_form4_filings(
        self, cik10: str, *, since: datetime | None
    ) -> list[tuple[str, str]]:
        resp = self._client.get(_SUBMISSIONS_URL.format(cik10=cik10))
        time.sleep(_RATE_LIMIT_SECONDS)
        if resp.status_code != 200:
            logger.warning("Form 4: submissions fetch failed (%s) for %s", resp.status_code, cik10)
            return []

        recent = resp.json().get("filings", {}).get("recent", {})
        out: list[tuple[str, str]] = []
        for form, filed, accession, primary_doc in zip(
            recent.get("form", []),
            recent.get("filingDate", []),
            recent.get("accessionNumber", []),
            recent.get("primaryDocument", []),
            strict=False,
        ):
            if form != "4":
                continue
            filed_at = datetime.strptime(filed, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            if since is not None and filed_at < since:
                continue
            out.append((accession, primary_doc))
        return out

    @staticmethod
    def _xml_filename(primary_doc: str) -> str:
        """Strip EDGAR's XSL renderer prefix to get the raw XML filename.

        `primaryDocument` points at the human-readable rendering, e.g.
        `xslF345X06/wk-form4_1787349005.xml`. The machine-readable original sits
        beside it at the same name without the prefix. The filename itself
        varies by filing agent, `form4.xml` for some, `wk-form4_<id>.xml` for
        others, so it cannot be hardcoded, which is what this method exists to
        stop anyone doing again.
        """
        return primary_doc.rsplit("/", 1)[-1] if primary_doc else "form4.xml"

    def _parse_filing(
        self, ticker: str, cik10: str, accession: str, primary_doc: str
    ) -> list[StructuredFactDTO]:
        accession_nodash = accession.replace("-", "")
        base = f"{_ARCHIVES_BASE}/{str(int(cik10))}/{accession_nodash}"
        url = f"{base}/{self._xml_filename(primary_doc)}"

        resp = self._client.get(url)
        time.sleep(_RATE_LIMIT_SECONDS)
        if resp.status_code != 200:
            logger.info("Form 4: document fetch returned %s for %s", resp.status_code, url)
            return []

        root = ET.fromstring(resp.text)
        owner = self._owner_details(root)

        facts: list[StructuredFactDTO] = []
        for index, node in enumerate(root.iterfind(".//nonDerivativeTransaction")):
            fact = self._transaction_fact(
                ticker, node, owner=owner, accession=accession, index=index, url=url
            )
            if fact is not None:
                facts.append(fact)
        return facts

    @staticmethod
    def _text(node, path: str) -> str | None:
        found = node.find(path)
        return found.text.strip() if found is not None and found.text else None

    @classmethod
    def _owner_details(cls, root) -> dict:
        relationship = root.find(".//reportingOwnerRelationship")
        is_true = lambda field: (  # noqa: E731
            relationship is not None
            and (cls._text(relationship, field) or "").lower() in ("1", "true")
        )
        return {
            "owner": cls._text(root, ".//rptOwnerName"),
            "is_officer": is_true("isOfficer"),
            "is_director": is_true("isDirector"),
            "is_ten_percent_owner": is_true("isTenPercentOwner"),
            "officer_title": (
                cls._text(relationship, "officerTitle") if relationship is not None else None
            ),
        }

    @classmethod
    def _transaction_fact(
        cls, ticker: str, node, *, owner: dict, accession: str, index: int, url: str
    ) -> StructuredFactDTO | None:
        date_text = cls._text(node, "transactionDate/value")
        shares_text = cls._text(node, "transactionAmounts/transactionShares/value")
        if not date_text or not shares_text:
            return None

        try:
            as_of = datetime.strptime(date_text, "%Y-%m-%d").replace(tzinfo=timezone.utc)
            shares = float(shares_text)
        except ValueError:
            return None

        code = cls._text(node, "transactionCoding/transactionCode") or "?"
        disposed = (
            cls._text(node, "transactionAmounts/transactionAcquiredDisposedCode/value") or ""
        ).upper() == "D"
        price_text = cls._text(node, "transactionAmounts/transactionPricePerShare/value")
        price = float(price_text) if price_text else None

        # Signed shares: negative for a disposal. Rules then sum a window
        # rather than each re-deriving direction from a letter code.
        signed_shares = -shares if disposed else shares

        return StructuredFactDTO(
            company_ticker=ticker,
            fact_type="insider_transaction",
            source_name=SecForm4Adapter.source_name,
            as_of_date=as_of,
            value=signed_shares,
            unit="shares",
            source_url=url,
            attributes={
                **owner,
                "transaction_code": code,
                "transaction_meaning": _CODE_MEANINGS.get(code, "other"),
                "is_open_market": code in OPEN_MARKET_CODES,
                "disposed": disposed,
                "price_per_share": price,
                "value_usd": round(abs(signed_shares) * price, 2) if price else None,
                "security": cls._text(node, "securityTitle/value"),
                "shares_owned_after": cls._text(
                    node, "postTransactionAmounts/sharesOwnedFollowingTransaction/value"
                ),
                "accession_number": accession,
            },
        )


def content_hash(dto: StructuredFactDTO) -> str:
    """Stable identity for one transaction line within one filing.

    Two officers can file identical-looking trades on the same date, so the
    accession and the line's position within it are both part of the key.
    """
    attributes = dto.attributes
    parts = [
        attributes.get("accession_number") or "",
        attributes.get("owner") or "",
        attributes.get("transaction_code") or "",
        str(dto.value),
        str(attributes.get("price_per_share")),
        dto.as_of_date.date().isoformat(),
    ]
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()
