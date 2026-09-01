"""Unit tests for the pure-function pieces of ingestion/registry.py, the
content-hash dedupe key that the Phase 1 verification steps rely on.
"""

from app.ingestion.registry import _content_hash


def test_content_hash_is_deterministic():
    assert _content_hash("hello world") == _content_hash("hello world")


def test_content_hash_differs_for_different_content():
    assert _content_hash("filing A") != _content_hash("filing B")


import threading
import time

from app.ingestion import registry
from app.ingestion.registry import TickerIngestResult, ingest_many


class _FakeSession:
    """Stands in for a SQLAlchemy Session, and records that each worker got its
    own. Sharing one session across threads is the failure mode these tests
    exist to rule out."""

    opened: list[int] = []
    closed: int = 0
    _lock = threading.Lock()

    def __init__(self):
        with _FakeSession._lock:
            _FakeSession.opened.append(threading.get_ident())

    def close(self):
        with _FakeSession._lock:
            _FakeSession.closed += 1


def _install_fakes(monkeypatch, fetch):
    _FakeSession.opened = []
    _FakeSession.closed = 0
    monkeypatch.setattr(registry, "SessionLocal", _FakeSession)
    monkeypatch.setattr(registry, "ingest_all", fetch)


def test_ingest_many_returns_results_in_input_order(monkeypatch):
    """Workers finish in whatever order their sources respond, but a caller's
    log should read the same way twice."""

    def fetch(ticker, db, since=None):
        # Reverse the natural completion order: the last ticker returns first.
        time.sleep(0.02 if ticker == "AAPL" else 0.0)
        return {"sec_edgar": len(ticker)}

    _install_fakes(monkeypatch, fetch)
    results = ingest_many(["AAPL", "MSFT", "NVDA"], max_workers=3)

    assert [r.ticker for r in results] == ["AAPL", "MSFT", "NVDA"]
    assert all(r.ok for r in results)
    assert results[0].total_new == 4


def test_one_failing_ticker_does_not_stop_the_batch(monkeypatch):
    def fetch(ticker, db, since=None):
        if ticker == "BROKEN":
            raise RuntimeError("source is down")
        return {"sec_edgar": 1}

    _install_fakes(monkeypatch, fetch)
    results = ingest_many(["AAPL", "BROKEN", "NVDA"], max_workers=3)

    assert [r.ok for r in results] == [True, False, True]
    assert "source is down" in results[1].error
    assert results[1].counts == {}


def test_every_worker_gets_its_own_session(monkeypatch):
    def fetch(ticker, db, since=None):
        return {"sec_edgar": 0}

    _install_fakes(monkeypatch, fetch)
    ingest_many(["A", "B", "C", "D"], max_workers=4)

    # One session per ticker, every one closed, including the failed paths.
    assert len(_FakeSession.opened) == 4
    assert _FakeSession.closed == 4


def test_sessions_are_closed_even_when_ingestion_raises(monkeypatch):
    def fetch(ticker, db, since=None):
        raise RuntimeError("boom")

    _install_fakes(monkeypatch, fetch)
    ingest_many(["A", "B"], max_workers=2)

    assert _FakeSession.closed == 2


def test_tickers_actually_run_concurrently(monkeypatch):
    """Four tickers that each block for 100ms take 400ms sequentially. The
    point of the executor is that they do not."""
    barrier = threading.Barrier(4, timeout=5)

    def fetch(ticker, db, since=None):
        barrier.wait()
        return {"sec_edgar": 1}

    _install_fakes(monkeypatch, fetch)
    # The barrier only clears if all four are in flight at once; a sequential
    # implementation deadlocks here and fails on the timeout.
    results = ingest_many(["A", "B", "C", "D"], max_workers=4)
    assert all(r.ok for r in results)


def test_single_worker_runs_sequentially(monkeypatch):
    """max_workers=1 has to be a real escape hatch, not a pool of one."""
    order = []

    def fetch(ticker, db, since=None):
        order.append(ticker)
        return {}

    _install_fakes(monkeypatch, fetch)
    ingest_many(["A", "B", "C"], max_workers=1)
    assert order == ["A", "B", "C"]


def test_empty_ticker_list_does_no_work(monkeypatch):
    def fetch(ticker, db, since=None):
        raise AssertionError("should not be called")

    _install_fakes(monkeypatch, fetch)
    assert ingest_many([]) == []


def test_result_reports_total_across_adapters():
    result = TickerIngestResult(ticker="AAPL", counts={"sec_edgar": 3, "finnhub": 2})
    assert result.total_new == 5
    assert result.ok
