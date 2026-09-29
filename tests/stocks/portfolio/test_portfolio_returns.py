from datetime import date
from decimal import Decimal

import pytest

from app.stocks.portfolio import (
    PortfolioError,
    Transaction,
    account_xirr,
    external_flows,
    twr,
    valuations,
    xirr,
)
from portfolio_support import D, Fx, at, d, no_fx


def test_twr_removes_the_effect_of_a_deposit() -> None:
    values = [(d("2026-01-01"), D(1000)), (d("2026-01-02"), D(1100)), (d("2026-01-03"), D(2310))]
    # Day 2: +10%. Day 3: 1000 arrives at the start, 2100 -> 2310 is +10%.
    flows = [(d("2026-01-03"), D(1000))]
    assert twr(values, flows) == D("0.21")


def test_twr_with_withdrawal_and_same_day_flows_summed() -> None:
    values = [(d("2026-01-01"), D(1000)), (d("2026-01-02"), D(450))]
    flows = [(d("2026-01-02"), D(-600)), (d("2026-01-02"), D(100))]
    # Start of day 2: 1000 - 500 = 500; ends at 450: -10%.
    assert twr(values, flows) == D("-0.1")


def test_twr_from_an_empty_account() -> None:
    values = [(d("2026-01-01"), D(0)), (d("2026-01-02"), D(0)), (d("2026-01-03"), D(1050))]
    assert twr(values, [(d("2026-01-03"), D(1000))]) == D("0.05")


@pytest.mark.parametrize(
    ("values", "flows", "problem"),
    [
        ([(d("2026-01-01"), D(1))], [], "at least two"),
        ([(d("2026-01-02"), D(1)), (d("2026-01-01"), D(1))], [], "strictly increasing"),
        (
            [(d("2026-01-01"), D(1)), (d("2026-01-02"), D(1))],
            [(d("2026-01-01"), D(1))],
            "no valuation after",
        ),
        (
            [(d("2026-01-01"), D(1)), (d("2026-01-03"), D(1))],
            [(d("2026-01-02"), D(1))],
            "no valuation after",
        ),
        ([(d("2026-01-01"), D(0)), (d("2026-01-02"), D(5))], [], "from nothing"),
        (
            [(d("2026-01-01"), D(1)), (d("2026-01-02"), D(0))],
            [(d("2026-01-02"), D(-2))],
            "negative starting value",
        ),
    ],
)
def test_twr_rejects_bad_input(
    values: list[tuple[date, Decimal]], flows: list[tuple[date, Decimal]], problem: str
) -> None:
    with pytest.raises(PortfolioError, match=problem):
        twr(values, flows)


def test_xirr_one_year() -> None:
    assert xirr([(d("2025-01-01"), D(-1000)), (d("2026-01-01"), D(1100))]) == D("0.1")


def test_xirr_loss() -> None:
    assert xirr([(d("2025-01-01"), D(-1000)), (d("2026-01-01"), D(500))]) == D("-0.5")


def test_xirr_matches_the_excel_documentation_example() -> None:
    flows = [
        (d("2008-01-01"), D(-10000)),
        (d("2008-03-01"), D(2750)),
        (d("2008-10-30"), D(4250)),
        (d("2009-02-15"), D(3250)),
        (d("2009-04-01"), D(2750)),
    ]
    # Microsoft's XIRR help page gives 0.373362535.
    assert abs(xirr(flows) - D("0.373362535")) < D("1e-8")


def test_xirr_near_total_loss_and_huge_gain() -> None:
    assert xirr([(d("2025-01-01"), D(-1000)), (d("2026-01-01"), D("0.01"))]) == D("-0.99999")
    assert xirr([(d("2026-01-01"), D(-1)), (d("2026-01-31"), D(2))]) > D(1000)


def test_xirr_picks_the_root_nearest_zero() -> None:
    # -100, +235, -137.76 over whole non-leap years has roots at 12% and 23%.
    flows = [
        (d("2021-01-01"), D(-100)),
        (d("2022-01-01"), D(235)),
        (d("2023-01-01"), D("-137.76")),
    ]
    assert xirr(flows) == D("0.12")


def test_xirr_rejects_one_sided_flows() -> None:
    with pytest.raises(PortfolioError, match="positive and one negative"):
        xirr([(d("2025-01-01"), D(-1000)), (d("2026-01-01"), D(0))])


def test_xirr_without_a_solution() -> None:
    # Money back before it goes in, with no rate that balances within the grid.
    flows = [(d("2020-01-01"), D(1)), (d("2020-01-02"), D(-1000000))]
    with pytest.raises(PortfolioError, match="did not find"):
        xirr(flows)


def test_account_xirr_and_flows() -> None:
    fx = Fx({"USD": {"2025-01-01": "0.5"}})
    txs = [
        Transaction.deposit(at("2025-01-01"), D(1000)),
        Transaction.deposit(at("2025-07-01"), D(100), "USD"),
        Transaction.withdrawal(at("2025-07-01"), D(50)),
        Transaction.buy(at("2025-01-02"), "A", D(1), D(1), "EUR"),
    ]
    assert external_flows(txs, d("2026-01-01"), fx) == [
        (d("2025-01-01"), D(1000)),
        (d("2025-07-01"), D(0)),
    ]
    only_deposit = txs[:1]
    assert account_xirr(only_deposit, d("2026-01-01"), D(1100), no_fx) == D("0.1")


def test_valuations_price_holdings_each_day() -> None:
    txs = [
        Transaction.deposit(at("2026-01-01"), D(1000)),
        Transaction.buy(at("2026-01-02"), "A", D(10), D(100), "EUR"),
    ]
    prices = {d("2026-01-02"): D(100), d("2026-01-03"): D(110)}
    got = valuations(
        txs,
        [d("2026-01-03"), d("2026-01-01"), d("2026-01-02")],
        lambda day, _key: prices[day],
        no_fx,
    )
    assert got == [
        (d("2026-01-01"), D(1000)),
        (d("2026-01-02"), D(1000)),
        (d("2026-01-03"), D(1100)),
    ]
    assert twr(got, external_flows(txs, d("2026-01-03"), no_fx)[1:]) == D("0.1")
