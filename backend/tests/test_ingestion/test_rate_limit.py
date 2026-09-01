"""The rate limiter is the thing that makes concurrent ingestion safe, so its
guarantee is worth testing directly: no matter how many threads ask, requests
to one host leave at most one per interval.
"""

import threading
import time

import pytest

from app.ingestion.rate_limit import (
    SEC_INTERVAL_SECONDS,
    HostRateLimiter,
    bucket_for,
    limiter,
)


@pytest.fixture
def clean_limiter():
    lim = HostRateLimiter(default_interval=0.05)
    yield lim
    lim.reset()


def test_first_request_to_a_host_does_not_wait(clean_limiter):
    assert clean_limiter.acquire("https://example.com/a") == 0.0


def test_second_request_waits_for_the_interval(clean_limiter):
    clean_limiter.acquire("https://example.com/a")
    started = time.monotonic()
    clean_limiter.acquire("https://example.com/b")
    assert time.monotonic() - started >= 0.04


def test_different_hosts_do_not_block_each_other(clean_limiter):
    clean_limiter.acquire("https://example.com/a")
    assert clean_limiter.acquire("https://other.example.org/a") == 0.0


def test_sec_hostnames_share_one_bucket():
    """Three adapters hit SEC through different hostnames with no knowledge of
    each other. If those were separate buckets the fair-access ceiling would be
    exceeded by exactly the number of SEC hostnames in use."""
    assert bucket_for("https://www.sec.gov/Archives/x") == "sec.gov"
    assert bucket_for("https://data.sec.gov/submissions/y") == "sec.gov"
    assert bucket_for("https://sec.gov/files/z") == "sec.gov"
    assert bucket_for("https://finance.yahoo.com/q") == "finance.yahoo.com"


def test_sec_bucket_uses_the_sec_interval():
    assert limiter.interval_for("sec.gov") == SEC_INTERVAL_SECONDS


def test_concurrent_callers_are_paced_in_aggregate(clean_limiter):
    """The bug this module exists to prevent: N threads each pacing themselves
    produce N times the intended request rate. Twelve acquisitions at a 50ms
    interval cannot legitimately finish in under 550ms however they are
    spread across threads."""
    calls = 12
    interval = 0.05
    barrier = threading.Barrier(4)

    def worker():
        barrier.wait()
        for _ in range(calls // 4):
            clean_limiter.acquire("https://example.com/x")

    started = time.monotonic()
    threads = [threading.Thread(target=worker) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    elapsed = time.monotonic() - started

    assert elapsed >= (calls - 1) * interval


def test_waiting_happens_outside_the_lock(clean_limiter):
    """A limiter that slept while holding its lock would serialise every host
    behind whichever one is waiting, which would quietly undo the concurrency
    it is supposed to permit."""
    clean_limiter.acquire("https://slow.example.com/a")

    def occupy():
        clean_limiter.acquire("https://slow.example.com/b")

    thread = threading.Thread(target=occupy)
    thread.start()
    time.sleep(0.01)

    started = time.monotonic()
    clean_limiter.acquire("https://fast.example.org/a")
    assert time.monotonic() - started < 0.02

    thread.join()


def test_scraper_throttle_uses_the_shared_limiter(monkeypatch):
    """BaseScraper used to keep its own per-instance limiter whose lock was
    held across the sleep. Under concurrent ingestion that made a wait for one
    domain block requests to every other domain, so the pool bought nothing.
    The behaviour is worth pinning: throttling must go through the shared
    limiter, which waits outside its lock.
    """
    from app.ingestion.scrapers.base_scraper import BaseScraper

    seen: list[str] = []
    monkeypatch.setattr(
        "app.ingestion.scrapers.base_scraper.limiter.acquire",
        lambda url: seen.append(url) or 0.0,
    )

    scraper = BaseScraper()
    scraper._throttle("https://example.com/page")

    assert seen == ["https://example.com/page"]
    assert not hasattr(scraper, "_last_request_at")


def test_no_acquisition_departs_before_its_reserved_slot(clean_limiter):
    """The guarantee is about the schedule, not about the gap between any two
    observations. Reservations are handed out `interval` apart from the first
    call, so the Nth acquisition cannot legitimately complete before
    (N-1) * interval has elapsed. Measuring consecutive gaps instead would be
    testing scheduler jitter: a call that returns a few milliseconds late
    shortens the gap to its successor without either one arriving early.
    """
    calls = 12
    interval = 0.05

    started = time.monotonic()
    for _ in range(calls):
        clean_limiter.acquire("https://example.com/x")
    elapsed = time.monotonic() - started

    assert elapsed >= (calls - 1) * interval


def test_acquire_does_not_return_before_the_slot_it_reserved(clean_limiter):
    """time.sleep is allowed to return early and measurably does. Sleeping
    once let a call return a few milliseconds before its slot, which is
    harmless individually and is exactly the drift that erodes a rate budget
    over a long run."""
    clean_limiter.acquire("https://example.com/x")

    for _ in range(20):
        before = time.monotonic()
        waited = clean_limiter.acquire("https://example.com/x")
        assert time.monotonic() - before >= waited
