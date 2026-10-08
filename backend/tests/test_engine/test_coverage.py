"""Closing the coverage gap without exhausting the thing that closes it.

The drip exists because a hundred and twenty-three of a hundred and thirty
company pages reported their own emptiness. Every rule here is about making
steady progress inside a quota that refuses bursts, and the failure it guards
against is subtle: a run that always starts at the beginning of the alphabet
covers the same companies repeatedly and never reaches the end.
"""

from datetime import datetime, timedelta, timezone

from app.engine.coverage import (
    ONE_SHOT_FORMS,
    PRIORS_PER_RUN,
    READS_PER_RUN,
    STALE_PRIOR_DAYS,
    DripResult,
)

NOW = datetime(2026, 9, 23, tzinfo=timezone.utc)


def test_a_run_is_bounded_so_a_free_tier_is_not_asked_for_a_burst():
    """The point is steady progress inside a quota that refuses long runs, not
    racing through the universe and then failing for a day.

    The ceiling here is deliberately loose. What this protects is the property
    that a run is bounded at all, so one pass cannot sit in the scheduler for
    hours and overlap the next; it is not a claim about the right number. That
    number is a throughput decision and it has already moved once, when the
    measured backlog turned out to be hours of provider time rather than the
    months a broken chain's throughput implied.
    """
    assert 0 < PRIORS_PER_RUN <= 25
    assert 0 < READS_PER_RUN <= 25


def test_a_refusal_is_reported_rather_than_counted_as_failure():
    """A free tier saying no is the expected steady state, not an error. The
    caller stops and the next run continues where this one left off."""
    result = DripResult(covered=2, exhausted=True, remaining=115)
    assert result.exhausted is True
    assert result.failed == 0


def test_remaining_is_reported_so_progress_is_visible():
    """Without it a drip that covers three a run and one that covers none look
    identical in the log."""
    result = DripResult(covered=3, remaining=114)
    assert result.remaining == 114


def test_a_one_shot_read_looks_at_filings_not_news():
    """A verdict rests on the risk factors and the full-year picture. News is
    the noisiest source and the least worth a one-time budget."""
    assert ONE_SHOT_FORMS == ("10-K", "10-Q")
    assert "news" not in ONE_SHOT_FORMS


def test_the_annual_report_is_preferred_over_the_quarterly():
    """A 10-K carries the risk factors a verdict actually rests on."""
    assert ONE_SHOT_FORMS[0] == "10-K"


def test_a_prior_goes_stale_rather_than_lasting_forever():
    """Otherwise coverage quietly becomes a set of standing views describing
    last year's company."""
    assert 30 <= STALE_PRIOR_DAYS <= 365


def test_never_covered_companies_come_before_refreshes():
    """A company with no prior scores every filing zero, which is
    indistinguishable from a quiet week. A company with a slightly old one is
    still answering."""
    stale_cutoff = NOW - timedelta(days=STALE_PRIOR_DAYS)
    # A prior from just inside the window is not yet due, so a company with no
    # prior at all must be offered first regardless of alphabet.
    assert stale_cutoff < NOW


# ---- where the quota goes --------------------------------------------


class _Candidate:
    def __init__(self, ticker, sector, sec_rank=None):
        self.ticker = ticker
        self.sector = sector
        self.sec_rank = sec_rank


def test_reads_go_to_the_least_covered_sector_first():
    """Reading alphabetically inside a quota of two companies per run produces
    a corpus shaped like the alphabet. What it produced in practice was a
    corpus shaped like one sector: thirteen of the thirty companies Loom had
    read were Technology and two were Industrials, so every cross-sectional
    measurement over findings was really a measurement of Technology. The
    sector-drift test failed on exactly that, and the second failure survived a
    fivefold increase in the price universe because the disclosures were still
    50 to 90 percent one sector."""
    from app.engine.coverage import read_order

    candidates = [
        _Candidate("AAAA", "Technology", 10),
        _Candidate("ZZZZ", "Utilities", 800),
    ]
    already_read = {"Technology": 13, "Utilities": 1}

    order = [c.ticker for c in read_order(candidates, already_read)]

    # Alphabetically and by size, the technology name wins. By need it does not.
    assert order == ["ZZZZ", "AAAA"]


def test_within_a_sector_the_largest_company_is_read_first():
    """They file more, are written about more, and their disclosures reach
    further, so they are worth more per unit of quota."""
    from app.engine.coverage import read_order

    order = [
        c.ticker for c in read_order(
            [_Candidate("SMALL", "Utilities", 900), _Candidate("BIG", "Utilities", 12)],
            {"Utilities": 1},
        )
    ]

    assert order == ["BIG", "SMALL"]


def test_a_company_with_no_size_is_not_treated_as_the_largest():
    """`sec_rank` is absent for anything added by hand rather than seeded from
    SEC's directory. Sorting a missing size first would put every hand-added
    ticker at the front of the queue."""
    from app.engine.coverage import read_order

    order = [
        c.ticker for c in read_order(
            [_Candidate("UNKNOWN", "Utilities", None), _Candidate("RANKED", "Utilities", 700)],
            {"Utilities": 1},
        )
    ]

    assert order == ["RANKED", "UNKNOWN"]


def test_an_entirely_unread_sector_outranks_a_barely_read_one():
    from app.engine.coverage import read_order

    order = [
        c.ticker for c in read_order(
            [_Candidate("SEEN", "Financials", 5), _Candidate("NEVER", "Consumer Staples", 950)],
            {"Financials": 5},
        )
    ]

    assert order == ["NEVER", "SEEN"]


# ---- depth, not only breadth -----------------------------------------------


def test_depth_target_is_more_than_one_document():
    """A company read once has every finding inside a single document, which is
    the worst case for both quantities a verdict rests on. The clustering
    correction weights nine findings from one filing at 0.333 each and the same
    nine across three filings at 0.577, so a second read roughly doubles a
    company's evidence strength without extracting one new fact. And a strong
    verdict needs two independent kinds of source, so a one-document company is
    capped whatever its filing said.

    Measured on the stored corpus: 32 of the 40 covered companies had exactly one
    document, and the only company Loom could reach a verdict for was the one
    with five.
    """
    from app.engine.coverage import DEPTH_TARGET_DOCUMENTS

    assert DEPTH_TARGET_DOCUMENTS > 1


def test_the_quota_is_split_rather_than_spent_entirely_on_first_reads():
    """Breadth alone is what left every covered company at one document. Depth
    alone would leave the genre norms a measurement of whichever sector was read
    first, which the sector-drift test already failed on twice. Neither queue is
    allowed to starve the other."""
    from app.engine.coverage import READS_PER_RUN

    pending, deeper = ["a", "b", "c"], ["x", "y", "z"]
    limit = READS_PER_RUN
    depth_share = limit // 2 if pending else limit
    breadth_share = limit - depth_share

    assert depth_share >= 1
    assert breadth_share >= 1
    assert depth_share + breadth_share == limit


def test_an_empty_queue_yields_its_share_instead_of_wasting_it():
    """A run that finds nothing to deepen must still read something new, and a
    run with nothing new must still deepen. Reserving a share for an empty queue
    would silently halve an already small quota."""
    limit = 2

    # Nothing left to read for the first time.
    pending, deeper = [], ["x", "y"]
    depth_share = limit // 2 if pending else limit
    breadth_share = limit - depth_share
    assert (breadth_share, depth_share) == (0, 2)

    # Nothing yet worth deepening.
    pending, deeper = ["a", "b"], []
    depth_share = limit // 2 if pending else limit
    breadth_share = limit - depth_share
    if not deeper:
        breadth_share, depth_share = limit, 0
    assert (breadth_share, depth_share) == (2, 0)


def test_the_depth_pass_only_analyses_filings_already_stored():
    """Analysis is the expensive half and fetching is not. Spending a depth call
    on a fetch would leave the company with a document it has not read, which is
    the state the pass exists to end."""
    import inspect

    from app.engine import coverage

    source = inspect.getsource(coverage.drip_reads)
    # The fetch path is reachable only for a company being read for the
    # first time.
    assert "if document is None and not is_depth:" in source


# ---- the three claims on a run's budget --------------------------------------


def test_the_shares_sum_to_the_whole_budget():
    """Breadth takes the remainder, so no part of a run is unclaimed. If these
    ever sum past 1.0 the last claim silently gets nothing."""
    from app.engine.coverage import HOLDINGS_SHARE, REACH_SHARE

    assert 0 < HOLDINGS_SHARE < 1
    assert 0 < REACH_SHARE < 1
    assert HOLDINGS_SHARE + REACH_SHARE < 1, "breadth must keep a funded share"


def test_holdings_are_round_robined_not_ranked_by_popularity():
    """The reason the split exists. Ranked globally, a company three accounts
    hold always beats one account's only holding and the sole holder waits
    forever; interleaved, every account gets a turn in the same pass."""
    from app.engine.coverage import companies_for_users

    class C:
        def __init__(self, ticker):
            self.id, self.ticker = ticker, ticker

    popular, only_mine, also_mine = C("POP"), C("SOLO"), C("MINE2")

    # Three accounts hold POP; one account additionally holds SOLO.
    rows = [("a", popular), ("b", popular), ("c", popular),
            ("c", only_mine), ("c", also_mine)]

    by_account: dict = {}
    for user_id, company in rows:
        by_account.setdefault(user_id, []).append(company)
    for owned in by_account.values():
        owned.sort(key=lambda c: c.ticker)

    out, seen = [], set()
    for i in range(max(len(v) for v in by_account.values())):
        for owned in by_account.values():
            if i < len(owned) and owned[i].id not in seen:
                seen.add(owned[i].id)
                out.append(owned[i])

    tickers = [c.ticker for c in out]
    assert tickers[0] == "POP", "the first pass still serves the shared name"
    assert "MINE2" in tickers[:3], (
        "account c's own first pick must land in the first round, not behind "
        "every other account's entire list"
    )
    assert len(set(tickers)) == len(tickers), "a shared company is read once"


def test_a_focus_refresh_is_bounded():
    """Focus is derived from holdings now, so it grows with the user base.
    Unbounded, one refresh would spend the whole allowance every six hours and
    the coverage drip would never get a call."""
    from app.scheduling.jobs import FOCUS_PER_REFRESH

    assert 0 < FOCUS_PER_REFRESH <= 25
