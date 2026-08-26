"""Threshold rule tests. No model, no database, no network, just arithmetic.

The rule that matters most here is the one about transaction codes. Real Form 4
data from Apple showed an officer "disposing of" 16,238 shares worth $4.8m on a
single day, which was shares withheld to cover tax on vesting equity, alongside
open-market sales eleven times smaller. A rule that treated those alike would
report insider selling at almost every large company every quarter.
"""

import uuid
from datetime import date, timedelta

from app.engine.fact_rules import (
    MIN_TRADE_VALUE_USD,
    evaluate,
    insider_buy_cluster,
    insider_sell_cluster,
    short_interest_spike,
)
from app.models.structured_fact import FactType, StructuredFact

TODAY = date(2026, 8, 20)


def _trade(
    *,
    owner: str,
    days_ago: int = 0,
    disposed: bool = True,
    code: str = "S",
    shares: float = 1_000,
    price: float = 300.0,
) -> StructuredFact:
    return StructuredFact(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        fact_type=FactType.INSIDER_TRANSACTION,
        source_name="sec-edgar-form4",
        as_of_date=TODAY - timedelta(days=days_ago),
        value=-shares if disposed else shares,
        unit="shares",
        attributes={
            "owner": owner,
            "transaction_code": code,
            "is_open_market": code in ("S", "P"),
            "disposed": disposed,
            "price_per_share": price,
            "value_usd": shares * price,
        },
        content_hash=uuid.uuid4().hex,
    )


def _short(*, value: float, days_ago: int) -> StructuredFact:
    return StructuredFact(
        id=uuid.uuid4(),
        company_id=uuid.uuid4(),
        fact_type=FactType.SHORT_INTEREST,
        source_name="finra",
        as_of_date=TODAY - timedelta(days=days_ago),
        value=value,
        unit="shares",
        attributes={},
        content_hash=uuid.uuid4().hex,
    )


# ---- insider selling ----------------------------------------------------


def test_three_insiders_selling_in_a_window_is_a_cluster():
    facts = [_trade(owner=name, days_ago=day) for name, day in
             [("Alice", 1), ("Bob", 4), ("Carla", 9)]]

    finding = insider_sell_cluster(facts)

    assert finding is not None
    assert finding.direction == "negative"
    assert sorted(finding.evidence["insiders"]) == ["Alice", "Bob", "Carla"]


def test_tax_withholding_is_not_insider_selling():
    """Code F is shares withheld to cover tax on vesting equity, a consequence
    of a compensation schedule rather than a decision to sell. This is the
    single most consequential filter in the file."""
    facts = [_trade(owner=name, days_ago=day, code="F", shares=20_000)
             for name, day in [("Alice", 1), ("Bob", 4), ("Carla", 9)]]

    assert insider_sell_cluster(facts) is None


def test_option_exercises_are_not_insider_selling():
    facts = [_trade(owner=name, days_ago=day, code="M")
             for name, day in [("Alice", 1), ("Bob", 4), ("Carla", 9)]]

    assert insider_sell_cluster(facts) is None


def test_gifts_are_not_insider_selling():
    facts = [_trade(owner=name, days_ago=day, code="G")
             for name, day in [("Alice", 1), ("Bob", 4), ("Carla", 9)]]

    assert insider_sell_cluster(facts) is None


def test_one_person_selling_repeatedly_is_not_a_cluster():
    """Agreement across people is the signal; one person selling three times is
    one liquidity decision."""
    facts = [_trade(owner="Alice", days_ago=day) for day in (1, 4, 9)]

    assert insider_sell_cluster(facts) is None


def test_sales_spread_beyond_the_window_do_not_cluster():
    facts = [_trade(owner=name, days_ago=day) for name, day in
             [("Alice", 1), ("Bob", 40), ("Carla", 80)]]

    assert insider_sell_cluster(facts) is None


def test_trivial_sales_are_ignored():
    facts = [_trade(owner=name, days_ago=day, shares=1, price=10.0)
             for name, day in [("Alice", 1), ("Bob", 4), ("Carla", 9)]]

    assert insider_sell_cluster(facts) is None
    # Sanity: the same trades above the floor do fire.
    big = [_trade(owner=name, days_ago=day, shares=MIN_TRADE_VALUE_USD, price=1.0)
           for name, day in [("Alice", 1), ("Bob", 4), ("Carla", 9)]]
    assert insider_sell_cluster(big) is not None


def test_insider_activity_is_never_reported_as_major():
    """Insider trades corroborate a thesis, they are not one on their own, and
    overstating them is how this data gets misread."""
    facts = [_trade(owner=f"Insider{i}", days_ago=i) for i in range(8)]

    finding = insider_sell_cluster(facts)
    assert finding is not None and finding.magnitude != "major"


# ---- insider buying -----------------------------------------------------


def test_insider_buying_is_a_separate_positive_signal():
    """Insiders sell for many reasons and buy for essentially one, so buys must
    never be averaged in with sells."""
    facts = [_trade(owner=name, days_ago=day, disposed=False, code="P")
             for name, day in [("Alice", 1), ("Bob", 4), ("Carla", 9)]]

    assert insider_sell_cluster(facts) is None
    buying = insider_buy_cluster(facts)
    assert buying is not None and buying.direction == "positive"


# ---- short interest -----------------------------------------------------


def test_short_interest_spike_needs_a_real_jump():
    assert short_interest_spike([_short(value=1_000_000, days_ago=30),
                                 _short(value=1_050_000, days_ago=1)]) is None

    finding = short_interest_spike([_short(value=1_000_000, days_ago=30),
                                    _short(value=1_600_000, days_ago=1)])
    assert finding is not None
    assert finding.evidence["increase_percent"] == 60.0
    assert finding.magnitude == "moderate"


def test_a_single_short_interest_reading_says_nothing():
    assert short_interest_spike([_short(value=5_000_000, days_ago=1)]) is None


def test_falling_short_interest_does_not_fire():
    assert short_interest_spike([_short(value=2_000_000, days_ago=30),
                                 _short(value=1_000_000, days_ago=1)]) is None


# ---- orchestration ------------------------------------------------------


def test_evaluate_runs_every_rule_independently():
    facts = [
        *[_trade(owner=name, days_ago=day) for name, day in
          [("Alice", 1), ("Bob", 4), ("Carla", 9)]],
        _short(value=1_000_000, days_ago=30),
        _short(value=1_600_000, days_ago=1),
    ]

    rules = {f.rule for f in evaluate(facts)}
    assert rules == {"insider_sell_cluster", "short_interest_spike"}


def test_evaluate_returns_nothing_on_a_quiet_company():
    assert evaluate([_trade(owner="Alice", days_ago=1)]) == []
