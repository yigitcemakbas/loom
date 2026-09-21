"""Tests for EDGAR full-text search and third-party filings.

Two things matter more than parsing. First, form filtering, which is the
difference between a source and a firehose: unfiltered, a search for a large
company returns mostly fund holdings disclosures. Second, attribution, because
a filing about a company written by someone else must never be readable as that
company's own statement.
"""

from datetime import date
from types import SimpleNamespace

import pytest

from app.ingestion.ecosystem import DOC_SUBTYPE, EcosystemMentionsAdapter
from app.ingestion.edgar_fts import (
    OPERATING_FORMS,
    EdgarFullTextSearch,
    FilingHit,
    _parse_hit,
    core_name,
)


def _raw(cik="0001769628", name="CoreWeave, Inc.  (CRWV)", form="8-K",
         accession="0001193125-26-000001", doc="ex991.htm", file_date="2026-09-17"):
    return {
        "_id": f"{accession}:{doc}",
        "_source": {
            "ciks": [cik], "display_names": [name], "root_forms": [form],
            "file_date": file_date, "items": ["2.02"], "file_description": "EX-99.1",
        },
    }


# ---- name handling ---------------------------------------------------------


def test_corporate_suffixes_are_stripped_before_searching():
    """Almost no filing writes "NVIDIA Corporation" when it means NVIDIA, so
    searching the full legal name finds far less than the short one."""
    assert core_name("NVIDIA CORPORATION") == "NVIDIA"
    assert core_name("Apple Inc.") == "Apple"
    assert core_name("Micron Technology, Inc.") == "Micron Technology"


def test_an_empty_name_searches_for_nothing_rather_than_everything():
    search = EdgarFullTextSearch()
    assert search.mentions_of("Inc.") == []


# ---- the noise filter ------------------------------------------------------


def test_operating_forms_exclude_holdings_filings():
    """The whole difference between 10,000 hits and 145. Every ETF holding a
    stock names it in NPORT-P, 13F-HR and N-CSR, which say nothing about the
    company's business."""
    for holding_form in ("NPORT-P", "13F-HR", "N-CSR", "SC 13G"):
        assert holding_form not in OPERATING_FORMS
    for operating_form in ("8-K", "10-K", "10-Q"):
        assert operating_form in OPERATING_FORMS


def test_search_defaults_to_operating_forms(monkeypatch):
    captured = {}

    search = EdgarFullTextSearch()

    def fake_get(url, params=None):
        captured["params"] = dict(params or [])
        return SimpleNamespace(status_code=200, json=lambda: {"hits": {"hits": []}})

    monkeypatch.setattr(search._client, "get", fake_get)
    search.search('"test"')
    assert "8-K" in captured["params"]["forms"]


# ---- parsing ---------------------------------------------------------------


def test_hit_resolves_to_the_exact_matching_document():
    """The `_id` is the only place the specific matching file is named; the
    source block describes the filing as a whole."""
    hit = _parse_hit(_raw())
    assert hit.accession == "0001193125-26-000001"
    assert hit.document == "ex991.htm"
    assert hit.url.endswith("/000119312526000001/ex991.htm")


def test_hit_without_an_identifier_is_skipped_not_raised():
    assert _parse_hit({"_source": {"ciks": ["1"]}}) is None
    assert _parse_hit({"_id": "no-colon", "_source": {"ciks": ["1"]}}) is None


def test_hit_without_a_filer_is_skipped():
    raw = _raw()
    raw["_source"]["ciks"] = []
    assert _parse_hit(raw) is None


def test_unparseable_date_does_not_lose_the_filing():
    raw = _raw(file_date="not-a-date")
    hit = _parse_hit(raw)
    assert hit is not None and hit.file_date is None


# ---- excluding the company's own filings -----------------------------------


def test_the_company_itself_is_excluded_from_its_own_mentions(monkeypatch):
    """EDGAR has no "not this filer" parameter, so the exclusion happens here.
    Without it the results are dominated by the company's own paperwork, which
    every other adapter already ingests."""
    search = EdgarFullTextSearch()
    own = _parse_hit(_raw(cik="0001045810", name="NVIDIA CORP"))
    other = _parse_hit(_raw(cik="0001769628", name="CoreWeave"))
    monkeypatch.setattr(search, "search", lambda *a, **k: [own, other])

    results = search.mentions_of("NVIDIA CORP", exclude_cik="0001045810")
    assert [h.cik for h in results] == ["0001769628"]


def test_one_filer_cannot_crowd_out_the_rest(monkeypatch):
    """The point is breadth of who is talking, not volume from any one of them."""
    search = EdgarFullTextSearch()
    hits = [_parse_hit(_raw(doc=f"ex{i}.htm")) for i in range(5)]
    monkeypatch.setattr(search, "search", lambda *a, **k: hits)
    assert len(search.mentions_of("NVIDIA", limit=10)) == 1


# ---- attribution -----------------------------------------------------------


def test_third_party_filings_are_marked_as_someone_elses(monkeypatch):
    """A risk in CoreWeave's 8-K is CoreWeave's view of its relationship with
    NVIDIA, not NVIDIA's disclosure about itself. Conflating them would
    attribute a supplier's pessimism to the company as if it had said it."""
    adapter = EcosystemMentionsAdapter()
    hit = FilingHit(
        accession="0001193125-26-000001", document="ex991.htm", cik="0001769628",
        company_name="CoreWeave, Inc.", form="8-K", file_date=date(2026, 9, 17),
        items=[], description=None,
    )
    monkeypatch.setattr(
        "app.ingestion.ecosystem.get_company_lookup_service",
        lambda: SimpleNamespace(lookup=lambda t: SimpleNamespace(
            name="NVIDIA CORP", cik="0001045810", ticker="NVDA")),
    )
    monkeypatch.setattr(adapter._search, "mentions_of", lambda *a, **k: [hit])
    monkeypatch.setattr(adapter, "_document_text", lambda url: "x" * 5000)

    doc = adapter.fetch("NVDA")[0]
    assert doc.doc_subtype == DOC_SUBTYPE
    assert doc.metadata["third_party"] is True
    assert doc.metadata["filed_by_name"] == "CoreWeave, Inc."
    assert doc.metadata["about_ticker"] == "NVDA"
    # The filer leads the title so a timeline cannot read as NVIDIA's own filing.
    assert doc.title.startswith("CoreWeave")


def test_stub_documents_are_discarded(monkeypatch):
    """Many hits resolve to a cover page or an exhibit reference rather than
    anything worth analysing."""
    adapter = EcosystemMentionsAdapter()
    hit = FilingHit("a-b-c", "x.htm", "0001769628", "CoreWeave", "8-K", date(2026, 9, 17), [], None)
    monkeypatch.setattr(
        "app.ingestion.ecosystem.get_company_lookup_service",
        lambda: SimpleNamespace(lookup=lambda t: SimpleNamespace(
            name="NVIDIA CORP", cik="0001045810", ticker="NVDA")),
    )
    monkeypatch.setattr(adapter._search, "mentions_of", lambda *a, **k: [hit])
    monkeypatch.setattr(adapter, "_document_text", lambda url: "too short")
    assert adapter.fetch("NVDA") == []
