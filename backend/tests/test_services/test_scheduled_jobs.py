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
        "refresh-briefs", "send-digests", "coverage-drip",
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
