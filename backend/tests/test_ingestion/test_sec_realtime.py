"""Tests for the live filing feed.

The parsing here is the single point where a live event either reaches the
engine or silently does not, so the tests lean on the failure modes rather than
the happy path. Both bugs pinned below were real and both were invisible: they
produced an empty result that looks exactly like a quiet market.
"""

import xml.etree.ElementTree as ET

from app.ingestion.sec_realtime import (
    DEFAULT_FORM_TYPES,
    SecRealtimeFeed,
    _parse_entry,
)

_ATOM = "http://www.w3.org/2005/Atom"


def _entry(title, href="https://www.sec.gov/Archives/edgar/data/1045810/000104581026000078/0001045810-26-000078-index.htm",
           updated="2026-09-18T17:30:09-04:00"):
    xml = f"""<entry xmlns="{_ATOM}">
      <title>{title}</title>
      <link rel="alternate" href="{href}"/>
      <updated>{updated}</updated>
    </entry>"""
    return ET.fromstring(xml)


def test_parses_a_hyphenated_form_type():
    """Every form worth watching contains a hyphen, so a pattern that splits on
    the first '-' rather than the first ' - ' matches nothing at all. That bug
    returned zero notices from a feed that was returning ten."""
    notice = _parse_entry(_entry("8-K - NVIDIA CORP (0001045810) (Filer)"))
    assert notice is not None
    assert notice.form == "8-K"
    assert notice.company_name == "NVIDIA CORP"
    assert notice.cik == "0001045810"


def test_parses_every_default_form_type():
    for form in DEFAULT_FORM_TYPES:
        notice = _parse_entry(_entry(f"{form} - SOME COMPANY INC (0000123456) (Filer)"))
        assert notice is not None and notice.form == form


def test_cik_is_zero_padded_to_match_stored_companies():
    """Companies store CIK zero-padded to ten. An unpadded match here would
    quietly never join, so every filing would look like it belonged to a
    company nobody tracks."""
    notice = _parse_entry(_entry("8-K - SMALL CO (0000885550) (Filer)"))
    assert notice.cik == "0000885550"
    assert len(notice.cik) == 10


def test_company_names_containing_a_dash_are_not_truncated():
    notice = _parse_entry(_entry("10-K - COCA-COLA CONSOLIDATED, INC. (0000317540) (Filer)"))
    assert notice.form == "10-K"
    assert notice.company_name == "COCA-COLA CONSOLIDATED, INC."


def test_accession_is_recovered_from_the_index_url():
    notice = _parse_entry(_entry("8-K - NVIDIA CORP (0001045810) (Filer)"))
    assert notice.accession == "0001045810-26-000078"


def test_amendments_are_identifiable():
    assert _parse_entry(_entry("8-K/A - X CORP (0000000001) (Filer)")).is_amendment
    assert not _parse_entry(_entry("8-K - X CORP (0000000001) (Filer)")).is_amendment


def test_unparseable_entries_are_skipped_not_raised():
    assert _parse_entry(_entry("this is not a filing title")) is None


def test_relative_links_are_absolutised():
    notice = _parse_entry(_entry(
        "8-K - X CORP (0000000001) (Filer)",
        href="/Archives/edgar/data/1/000000000126000001/0000000001-26-000001-index.htm",
    ))
    assert notice.index_url.startswith("https://www.sec.gov/")


def test_bad_timestamp_does_not_lose_the_filing():
    """Acceptance time is how latency is measured, but a filing with an
    unreadable timestamp is still a filing and must still be assessed."""
    notice = _parse_entry(_entry("8-K - X CORP (0000000001) (Filer)", updated="not-a-date"))
    assert notice is not None
    assert notice.accepted_at is None


def test_fetch_all_forms_deduplicates_across_form_queries(monkeypatch):
    """The feed is queried once per form type, and a filing can appear under
    more than one, so the same accession must not be assessed twice."""
    notice = _parse_entry(_entry("8-K - NVIDIA CORP (0001045810) (Filer)"))
    feed = SecRealtimeFeed(form_types=("8-K", "10-Q"))
    monkeypatch.setattr(feed, "fetch", lambda form_type=None, count=100: [notice])
    assert len(feed.fetch_all_forms()) == 1


def test_network_failure_costs_one_cycle_not_the_loop(monkeypatch):
    feed = SecRealtimeFeed()

    def boom(*a, **k):
        raise ConnectionError("EDGAR unreachable")

    monkeypatch.setattr(feed._client, "get", boom)
    assert feed.fetch() == []
