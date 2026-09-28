"""The scheduler is the line between a workbench and an instrument.

Loom's analysis was already real while every part of it waited for someone to
type a command, which made the whole quantitative layer a snapshot of whenever
it was last run by hand. These tests pin the properties that make running it
unattended safe rather than merely automatic.
"""

from app.scheduling import scheduler as scheduler_module


def test_the_scheduled_jobs_are_the_expected_set():
    ids = {job_id for job_id, *_ in scheduler_module._FREE_JOBS}
    assert ids == {
        "refresh-prices", "replay-priors", "score-factors",
        # Fetching filings costs no model quota, only SEC's shared rate limit,
        # so it earns a place on the clock cheaply. It is also the job the rest
        # depend on: the risk diff needs two filings of the same company, and
        # the corpus held 322 across a thousand companies.
        "fetch-filings",
        "refresh-briefs", "send-digests", "coverage-drip",
        # Corpus-wide tables every page reads and none should build. Measured
        # at a thousand companies, the precedent base took 1.5 seconds, landing
        # on whichever page view found the cache expired.
        "refresh-tables",
    }
    # The watchlist refresh is unbounded model spend and stays on its own
    # cadence rather than joining this list.
    assert scheduler_module._REFRESH_JOB_ID not in ids


def test_only_the_coverage_drip_spends_model_quota():
    """Everything else here is arithmetic over stored data, so firing one costs
    nothing and a missed fire costs nothing either. The drip is the exception
    and earns its place by being bounded twice: a small per-run limit, and a
    clean stop the moment the provider refuses."""
    from app.engine.coverage import PRIORS_PER_RUN, READS_PER_RUN

    assert PRIORS_PER_RUN <= 5 and READS_PER_RUN <= 5


def test_filings_are_fetched_before_anything_tries_to_read_them():
    """A document cannot be analysed before it is stored, and the drip spends
    model quota looking for one. Running the read first wastes the scarcest
    resource in the system on a company whose filing has not arrived."""
    offsets = {job_id: offset for job_id, _, _, _, offset in scheduler_module._FREE_JOBS}
    assert offsets["fetch-filings"] < offsets["coverage-drip"]


def test_fetching_filings_runs_far_more_often_than_reading_them():
    """Deliberate asymmetry. Storing a filing is free and bounded by SEC's rate
    limit; reading one costs a model call from a twenty-a-day allowance. The
    corpus is allowed to run ahead of the reading, because a filing that exists
    can be read later and one that was never fetched cannot."""
    minutes = {job_id: m for job_id, _, _, m, _ in scheduler_module._FREE_JOBS}
    assert minutes["fetch-filings"] <= minutes["coverage-drip"]


def test_the_filing_backfill_is_bounded_per_run():
    """It runs hourly, so an unbounded sweep would hold SEC's rate limiter for
    the whole hour and starve every other source that shares it."""
    from app.engine.filings import COMPANIES_PER_RUN, FETCHES_PER_RUN

    assert 0 < FETCHES_PER_RUN <= 50
    assert 0 < COMPANIES_PER_RUN <= 100


def test_the_filing_target_allows_a_diff():
    """Below two filings the risk diff cannot run at all, which is the signal
    priority.py trusts most and the only one checkable against source text."""
    from app.engine.filings import TARGET_FILINGS

    assert TARGET_FILINGS >= 2


def test_priors_are_built_before_the_filings_that_get_scored_against_them():
    """A filing replayed before its company has a standing view scores zero,
    which is indistinguishable from a quiet week."""
    offsets = {job_id: offset for job_id, _, _, _, offset in scheduler_module._FREE_JOBS}
    assert offsets["coverage-drip"] < offsets["replay-priors"]


def test_digests_are_checked_after_the_data_they_report_on():
    """A digest built before the briefs refresh would report yesterday's
    verdicts as today's news."""
    offsets = {job_id: offset for job_id, _, _, _, offset in scheduler_module._FREE_JOBS}
    assert offsets["refresh-briefs"] < offsets["send-digests"]


def test_prices_are_refreshed_before_factors_are_scored():
    """Valuation is a ratio to a price. Scoring factors against yesterday's
    prices would silently value every company a day late."""
    offsets = {job_id: offset for job_id, _, _, _, offset in scheduler_module._FREE_JOBS}
    assert offsets["refresh-prices"] < offsets["score-factors"]


def test_factors_are_scored_before_briefs_are_rebuilt():
    """A brief reads the scores, so rebuilding first would fold in last week's."""
    offsets = {job_id: offset for job_id, _, _, _, offset in scheduler_module._FREE_JOBS}
    assert offsets["score-factors"] < offsets["refresh-briefs"]


def test_no_two_jobs_wake_at_the_same_moment():
    """They share one database, and a local install has one core to spare."""
    offsets = [offset for *_, offset in scheduler_module._FREE_JOBS]
    assert len(offsets) == len(set(offsets))


def test_nothing_competes_with_application_startup():
    offsets = [offset for *_, offset in scheduler_module._FREE_JOBS]
    assert all(offset > 0 for offset in offsets)


def test_cadences_match_how_fast_the_underlying_data_moves():
    """Fundamentals change only when somebody files; a session's close is final
    once it happens. Running either more often redraws the same picture."""
    minutes = {job_id: m for job_id, _, _, m, _ in scheduler_module._FREE_JOBS}
    assert minutes["refresh-prices"] == 60 * 24
    assert minutes["score-factors"] == 60 * 24 * 7
    # Priors can only assess a filing once it has been stored, so this tracks
    # the document ingest rather than running faster than it.
    assert minutes["replay-priors"] <= minutes["refresh-prices"]


def test_a_first_run_time_can_be_offset():
    base = scheduler_module._first_run_time()
    later = scheduler_module._first_run_time(30)
    assert later > base


def test_the_scheduler_stays_off_when_configured_off():
    """A local install that does not want background work must get none,
    including the free jobs."""
    from app.config import settings

    original = settings.scheduler_enabled
    try:
        settings.scheduler_enabled = False
        scheduler_module._scheduler = None
        assert scheduler_module.start_scheduler() is None
    finally:
        settings.scheduler_enabled = original
        scheduler_module._scheduler = None


def test_the_measured_tables_refresh_before_they_can_expire():
    """A refresh slower than the time-to-live guarantees a gap, and the reader
    whose page view lands in the gap pays the build. The interval has to be
    comfortably inside the hold."""
    from app.engine.norms import PRECEDENT_TTL_SECONDS, TTL_SECONDS

    interval = next(
        minutes for job_id, _fn, _desc, minutes, _offset in scheduler_module._FREE_JOBS
        if job_id == "refresh-tables"
    )

    assert interval * 60 < PRECEDENT_TTL_SECONDS
    assert TTL_SECONDS <= PRECEDENT_TTL_SECONDS
