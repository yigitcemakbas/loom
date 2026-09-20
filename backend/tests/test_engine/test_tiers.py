"""Tests for the coverage tiers.

The tiers exist for one reason: a model call is the only resource here that
does not scale for free, so what each tier is allowed to spend is the whole
design. These tests pin the cost boundaries rather than the plumbing, because
a tier that quietly spends more than it should is indistinguishable from a
working one until the quota runs out.
"""

from types import SimpleNamespace

import pytest

from app.models.company import CompanyTier


def test_three_tiers_exist_and_are_ordered_by_cost():
    assert CompanyTier.FOCUS.value == "focus"
    assert CompanyTier.WATCH.value == "watch"
    assert CompanyTier.WIDE.value == "wide"


def test_only_focus_stores_a_document_corpus(monkeypatch):
    """A watched company reads filings during prior generation and keeps none.
    Persisting a corpus nothing will query would take on the focus tier's
    storage cost for none of its benefit."""
    from app.ingestion import registry

    for tier, expects_documents in (
        (CompanyTier.FOCUS, True),
        (CompanyTier.WATCH, False),
        (CompanyTier.WIDE, False),
    ):
        used: list = []

        company = SimpleNamespace(id="c1", ticker="X", tier=tier)
        monkeypatch.setattr(
            registry, "CompanyRepository",
            lambda db: SimpleNamespace(get_by_ticker=lambda t: company),
        )
        monkeypatch.setattr(
            registry, "DocumentRepository",
            lambda db: SimpleNamespace(newest_published_at=lambda cid: None),
        )
        monkeypatch.setattr(registry, "get_blob_store", lambda: None)
        monkeypatch.setattr(
            registry, "DOCUMENT_ADAPTERS",
            [SimpleNamespace(source_name="docs", fetch=lambda t, since=None: used.append("doc") or [])],
        )
        monkeypatch.setattr(registry, "FACT_ADAPTERS", [])

        registry.ingest_all("X", db=object())
        assert bool(used) is expects_documents, f"{tier.value} document handling is wrong"


def test_wide_tier_never_reaches_a_model(monkeypatch):
    """The cheapest tier's guarantee. If this ever fails, seeding a universe of
    a thousand names silently becomes a thousand model calls."""
    from app.engine import prior

    company = SimpleNamespace(id="c1", ticker="X", tier=CompanyTier.WIDE)
    monkeypatch.setattr(
        prior, "CompanyRepository",
        lambda db: SimpleNamespace(get_by_ticker=lambda t: company),
    )

    def fail(*a, **k):  # pragma: no cover - only runs on regression
        raise AssertionError("the wide tier must not call a model")

    monkeypatch.setattr(prior, "get_llm_client", fail)
    monkeypatch.setattr(prior, "_document_material", fail)

    assert prior.build_prior("X", db=object()) is None


def test_watch_tier_builds_its_prior_from_filings_not_findings(monkeypatch):
    """A watched company has no extracted findings by construction, so without
    the document path it would get no prior, and an unarmed watcher scores
    every filing zero and looks exactly like a quiet market."""
    from app.engine import prior

    company = SimpleNamespace(id="c1", ticker="X", tier=CompanyTier.WATCH)
    calls: list[str] = []

    monkeypatch.setattr(
        prior, "CompanyRepository",
        lambda db: SimpleNamespace(get_by_ticker=lambda t: company),
    )
    monkeypatch.setattr(
        prior, "SignalRepository",
        lambda db: SimpleNamespace(list_feed=lambda **k: []),
    )
    monkeypatch.setattr(
        prior, "FactRepository",
        lambda db: SimpleNamespace(
            latest_per_date=lambda e: [], earnings_events=lambda cid: [],
            list_for_company=lambda *a, **k: [],
        ),
    )
    monkeypatch.setattr(prior, "context_for", lambda t: None)
    monkeypatch.setattr(
        prior, "_document_material",
        lambda t: calls.append("documents") or "=== 10-K ===\nRisk factors text.",
    )
    monkeypatch.setattr(
        prior, "get_llm_client",
        lambda: SimpleNamespace(parse=lambda **k: None),
    )

    prior.build_prior("X", db=object())
    assert calls == ["documents"], "watch tier must read filings to build a prior"


def test_focus_falls_back_to_filings_when_analysis_has_not_run(monkeypatch):
    """A focus company whose analysis has not caught up should still get a
    prior. Having none is worse than having a rough one, because the watcher
    cannot tell an unarmed company from a calm one."""
    from app.engine import prior

    company = SimpleNamespace(id="c1", ticker="X", tier=CompanyTier.FOCUS)
    calls: list[str] = []

    monkeypatch.setattr(
        prior, "CompanyRepository",
        lambda db: SimpleNamespace(get_by_ticker=lambda t: company),
    )
    monkeypatch.setattr(
        prior, "SignalRepository",
        lambda db: SimpleNamespace(list_feed=lambda **k: []),
    )
    monkeypatch.setattr(
        prior, "FactRepository",
        lambda db: SimpleNamespace(
            latest_per_date=lambda e: [], earnings_events=lambda cid: [],
            list_for_company=lambda *a, **k: [],
        ),
    )
    monkeypatch.setattr(prior, "context_for", lambda t: None)
    monkeypatch.setattr(prior, "_document_material", lambda t: calls.append("documents") or "text")
    monkeypatch.setattr(prior, "get_llm_client", lambda: SimpleNamespace(parse=lambda **k: None))

    prior.build_prior("X", db=object())
    assert calls == ["documents"]


def test_nothing_to_build_from_yields_no_prior(monkeypatch):
    """No findings, no filings and no scheduled report is a genuine absence.
    Inventing a prior from it would arm the watcher with fiction."""
    from app.engine import prior

    company = SimpleNamespace(id="c1", ticker="X", tier=CompanyTier.WATCH)
    monkeypatch.setattr(
        prior, "CompanyRepository",
        lambda db: SimpleNamespace(get_by_ticker=lambda t: company),
    )
    monkeypatch.setattr(
        prior, "SignalRepository",
        lambda db: SimpleNamespace(list_feed=lambda **k: []),
    )
    monkeypatch.setattr(
        prior, "FactRepository",
        lambda db: SimpleNamespace(
            latest_per_date=lambda e: [], earnings_events=lambda cid: [],
            list_for_company=lambda *a, **k: [],
        ),
    )
    monkeypatch.setattr(prior, "context_for", lambda t: None)
    monkeypatch.setattr(prior, "_document_material", lambda t: "")

    def fail(*a, **k):  # pragma: no cover
        raise AssertionError("must not call a model with nothing to reason about")

    monkeypatch.setattr(prior, "get_llm_client", fail)
    assert prior.build_prior("X", db=object()) is None
