"""Tests for the exposure graph and event propagation.

The direction of an edge is the thing most worth pinning. Reversing it would
propagate every event to exactly the wrong set of companies and would look
entirely plausible while doing so.
"""

from types import SimpleNamespace

import pytest

from app.engine.exposure import (
    MAX_DEPENDENTS,
    MIN_MENTIONS,
    build_exposures,
)


class _Repo:
    def __init__(self, companies):
        self._companies = companies

    def list_all(self):
        return self._companies

    def get_by_id(self, cid):
        return next((c for c in self._companies if c.id == cid), None)


class _DB:
    def __init__(self):
        self.added = []

    def add(self, obj):
        self.added.append(obj)

    def commit(self):
        pass

    def execute(self, stmt):
        return SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: None, all=lambda: []))


def _company(cid, ticker, name, cik):
    return SimpleNamespace(id=cid, ticker=ticker, name=name, cik=cik)


NVDA = _company("n", "NVDA", "NVIDIA CORPORATION", "0001045810")
AMD = _company("a", "AMD", "ADVANCED MICRO DEVICES INC", "0000002488")
KO = _company("k", "KO", "COCA COLA CO", "0000021344")


def _build(monkeypatch, counts, companies=(NVDA, AMD, KO)):
    db = _DB()
    monkeypatch.setattr("app.engine.exposure.CompanyRepository", lambda d: _Repo(list(companies)))
    monkeypatch.setattr(
        "app.engine.exposure.EdgarFullTextSearch",
        lambda: SimpleNamespace(filer_counts=lambda q, **k: counts),
    )
    build_exposures(db, hub_tickers=["NVDA"])
    return db


def test_edge_points_from_dependent_to_hub(monkeypatch):
    """AMD naming NVIDIA means AMD is exposed to NVIDIA, not the reverse.
    NVIDIA barely needs to name AMD."""
    db = _build(monkeypatch, {AMD.cik: 136})
    assert len(db.added) == 1
    edge = db.added[0]
    assert edge.dependent_company_id == AMD.id
    assert edge.hub_company_id == NVDA.id
    assert edge.mention_count == 136


def test_incidental_mentions_are_not_a_dependency(monkeypatch):
    """Coca-Cola naming NVIDIA once is a passing reference. Semiconductor names
    manage 87 to 136, so the gap is wide enough that the exact cut matters less
    than having one."""
    db = _build(monkeypatch, {KO.cik: 1, AMD.cik: 136})
    assert [e.dependent_company_id for e in db.added] == [AMD.id]


def test_threshold_boundary_is_respected(monkeypatch):
    assert _build(monkeypatch, {AMD.cik: MIN_MENTIONS - 1}).added == []
    assert len(_build(monkeypatch, {AMD.cik: MIN_MENTIONS}).added) == 1


def test_a_company_is_never_its_own_dependent(monkeypatch):
    """Self-mentions dominate every result, because a company names itself in
    all of its own filings. NVIDIA's own bucket was 1,204 against AMD's 136."""
    db = _build(monkeypatch, {NVDA.cik: 1204, AMD.cik: 136})
    assert [e.dependent_company_id for e in db.added] == [AMD.id]


def test_untracked_filers_are_ignored(monkeypatch):
    """The aggregation covers every filer on EDGAR. Only companies in the
    universe can be acted on."""
    db = _build(monkeypatch, {"0009999999": 500, AMD.cik: 136})
    assert [e.dependent_company_id for e in db.added] == [AMD.id]


def test_propagation_is_capped(monkeypatch):
    """An event that supposedly moves forty positions is not a read, it is a
    market call."""
    many = {f"{i:010d}": 100 for i in range(1, 40)}
    companies = [NVDA] + [_company(f"c{i}", f"T{i}", f"Co {i}", f"{i:010d}") for i in range(1, 40)]
    db = _build(monkeypatch, many, companies=companies)
    assert len(db.added) == MAX_DEPENDENTS


def test_no_search_results_yields_no_edges(monkeypatch):
    assert _build(monkeypatch, {}).added == []
