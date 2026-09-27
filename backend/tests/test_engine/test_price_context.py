"""What the price has already done.

The founding constraint was "not a numeric price predictor", and it was read
once as "do not look at the price", with the result that Loom could describe
margin pressure in detail while having no idea the shares had already fallen a
fifth on it. Those are different things, and the line between them is what this
module tests.

Two failure modes, opposite in shape. Letting price decide direction turns the
verdict into momentum. Letting market silence suppress a finding deletes the
only thing reading filings can add.
"""

from datetime import date, timedelta

from app.engine.price_context import (
    MATERIAL_SIGMA,
    Move,
    move_after,
    moves_for,
    standing,
    typical_move,
)
from app.engine.quant.prices import Bar, PriceHistory

START = date(2025, 1, 6)


def _series(returns: list[float], *, start: date = START, first: float = 100.0) -> PriceHistory:
    """A daily series from a list of session returns, skipping weekends."""
    bars = []
    price = first
    when = start
    for daily in returns:
        while when.weekday() >= 5:
            when += timedelta(days=1)
        bars.append(Bar(session_date=when, close=price, adjusted_close=price))
        price *= 1 + daily
        when += timedelta(days=1)
    return PriceHistory(bars)


def _calm(n: int, drift: float = 0.0005) -> list[float]:
    """Alternating small moves, so the dispersion is small and known."""
    return [drift if i % 2 else -drift for i in range(n)]


# ---- materiality is company relative -----------------------------------


def test_the_same_move_is_material_for_a_calm_company_and_not_for_a_wild_one():
    """A four percent fortnight is enormous for a utility and ordinary for a
    small semiconductor company. One threshold across every ticker finds events
    in the volatile names and nothing anywhere else, which is a measure of
    volatility wearing the name of a measure of news."""
    shock = [0.0] * 10 + [0.006] * 10           # about 6% over the window

    calm = _series(_calm(200, 0.001) + shock)
    wild = _series(_calm(200, 0.02) + shock)
    when = calm.between(START, date(2030, 1, 1))[200].session_date

    calm_move = move_after(calm, None, when)
    wild_move = move_after(wild, None, when)

    assert calm_move.is_material
    assert not wild_move.is_material


def test_too_little_history_is_reported_as_unknown_not_as_ordinary():
    """A company Loom holds three weeks of prices for has not had an ordinary
    reaction, it has had an unmeasured one."""
    thin = _series(_calm(12) + [0.03] * 12)
    when = thin.between(START, date(2030, 1, 1))[12].session_date

    move = move_after(thin, None, when)

    assert move is not None
    assert move.sigma is None
    assert not move.is_material
    assert "too little history" in move.summary


def test_no_price_history_is_not_a_flat_reaction():
    assert move_after(None, None, date(2026, 1, 5)) is None


def test_the_baseline_cannot_contain_the_move_it_judges():
    """A volatility baseline measured through the event it is scoring absorbs
    the event, and the largest moves score as the most ordinary."""
    series = _series(_calm(200, 0.001) + [0.02] * 20)
    when = series.between(START, date(2030, 1, 1))[200].session_date

    baseline = typical_move(series, None, when)

    # Built from the calm stretch only, so it stays small.
    assert baseline is not None and baseline < 0.02


# ---- the market is removed before the move is judged --------------------


def test_a_market_wide_move_is_not_a_reaction_to_this_filing():
    """A stock that fell with everything else did not react to anything. This
    is the error the whole measure exists to avoid."""
    everyone_fell = _calm(200, 0.001) + [-0.01] * 12
    company = _series(everyone_fell)
    market = _series(everyone_fell)
    when = company.between(START, date(2030, 1, 1))[200].session_date

    raw = move_after(company, None, when)
    adjusted = move_after(company, market, when)

    assert raw.is_material
    assert abs(adjusted.abnormal_percent) < abs(raw.abnormal_percent)
    assert not adjusted.is_material


# ---- where the price stands --------------------------------------------


def test_a_company_at_its_high_is_described_as_having_priced_in_no_bad_news():
    rising = _series([0.004] * 260)
    as_of = rising.newest

    where = standing(rising, as_of)

    assert where.near_high
    assert "little bad news" in where.summary


def test_a_company_well_below_its_high_is_described_as_having_priced_in_some():
    fell = _series([0.004] * 200 + [-0.012] * 60)
    as_of = fell.newest

    where = standing(fell, as_of)

    assert where.drawdown >= 0.2
    assert "already reflected in the price" in where.summary


def test_standing_refuses_rather_than_guessing_from_nothing():
    assert standing(None, date(2026, 1, 5)) is None
    assert standing(_series([0.01]), date(2026, 1, 5)) is None


# ---- one move per document, not per finding -----------------------------


class _Finding:
    def __init__(self, ident, when):
        self.id = ident
        self.occurred_at = when


def test_findings_from_one_filing_share_one_measurement():
    """Forty findings from one annual report measure the same fortnight.
    Computing it forty times reaches the same answer forty times."""
    series = _series(_calm(200, 0.001) + [0.006] * 12)
    when = series.between(START, date(2030, 1, 1))[200].session_date
    from datetime import datetime, timezone
    at = datetime(when.year, when.month, when.day, tzinfo=timezone.utc)

    found = moves_for([_Finding(f"f{i}", at) for i in range(5)], series, None)

    assert len(found) == 5
    assert len({m.abnormal_percent for m in found.values()} ) == 1


def test_the_move_is_worded_as_a_fact_about_the_filing():
    """Saying "the price fell after this" against forty separate findings makes
    a causal claim about forty different things, and it cannot be true of any
    of them individually."""
    series = _series(_calm(200, 0.001) + [0.006] * 12)
    when = series.between(START, date(2030, 1, 1))[200].session_date

    move = move_after(series, None, when)

    assert "after this was filed" in move.summary


# ---- the channel the company-level measure cannot see ------------------


def test_a_sector_wide_move_is_invisible_to_the_company_level_measure():
    """The error this exists to fix, stated as a test.

    `move_after` reports the filer's return minus the market's, which is right
    for "did this filing move this company" and removes, by construction, any
    effect that hit a whole group. One company writes about AI capital spending,
    the entire complex reprices, and every company in it shows an abnormal
    return near zero. The measure records that nothing happened.
    """
    from app.engine.price_context import sector_move_after

    # The whole sector falls hard; the market does not.
    sector_fell = _calm(200, 0.001) + [-0.012] * 12
    market_flat = _calm(200, 0.001) + _calm(12, 0.001)

    filer = _series(sector_fell)
    peers = [_series(sector_fell) for _ in range(6)]
    market = _series(market_flat)
    when = filer.between(START, date(2030, 1, 1))[200].session_date

    company_level = move_after(filer, market, when)
    group_level = sector_move_after(filer, peers, market, when, "Technology")

    # The filer moved exactly with its peers, so against them it did nothing.
    assert abs(group_level.versus_peers_percent) < 1.0
    # But the group moved a great deal, which is the fact that was being lost.
    assert group_level.peers_percent < -8
    assert group_level.moved_together
    assert "companies Loom follows fell" in group_level.summary
    # The company-level measure sees the same event as this company's own.
    assert company_level.abnormal_percent < -8


def test_a_company_specific_move_is_attributed_to_the_company():
    from app.engine.price_context import sector_move_after

    quiet = _calm(200, 0.001) + _calm(12, 0.001)
    filer = _series(_calm(200, 0.001) + [-0.012] * 12)
    peers = [_series(quiet) for _ in range(6)]
    when = filer.between(START, date(2030, 1, 1))[200].session_date

    group_level = sector_move_after(filer, peers, _series(quiet), when, "Technology")

    assert abs(group_level.peers_percent) < 1.0
    assert group_level.versus_peers_percent < -8
    assert not group_level.moved_together


def test_a_handful_of_companies_is_not_a_sector():
    """A figure computed from three peers and presented as an industry invites
    a reader to conclude something from a coincidence."""
    from app.engine.price_context import MIN_PEERS, sector_move_after

    series = _series(_calm(220, 0.001))
    when = series.between(START, date(2030, 1, 1))[200].session_date

    too_few = [_series(_calm(220, 0.001)) for _ in range(MIN_PEERS - 1)]

    assert sector_move_after(series, too_few, None, when, "Technology") is None
