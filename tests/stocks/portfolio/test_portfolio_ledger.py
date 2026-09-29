"""FIFO lots and splits, with hand-computed expectations."""

import pytest

from app.stocks.portfolio import (
    InvalidTransaction,
    Lot,
    OversellError,
    Transaction,
    build_book,
    positions,
)
from portfolio_support import D, at, d

AS_OF = d("2026-12-31")


def test_partial_sell_across_lots() -> None:
    txs = [
        Transaction.buy(at("2026-01-05"), "A", D(10), D(100), "EUR", fee=D(5)),  # cost 1005
        Transaction.buy(at("2026-02-02"), "A", D(10), D(120), "EUR", fee=D(5)),  # cost 1205
        Transaction.sell(at("2026-03-02"), "A", D(15), D(130), "EUR", fee=D(5)),
    ]
    book = build_book(txs, AS_OF)

    [sale] = book.realisations
    # All of lot 1, half of lot 2: 1005 + 1205 * 5/10 = 1607.5.
    assert sale.lots == (
        Lot("A", d("2026-01-05"), D(10), D(1005)),
        Lot("A", d("2026-02-02"), D(5), D("602.5")),
    )
    assert sale.cost == D("1607.5")
    assert sale.proceeds == D(1945)  # 15 * 130 - 5
    assert sale.pnl == D("337.5")

    position = book.positions["A"]
    assert position.lots == (Lot("A", d("2026-02-02"), D(5), D("602.5")),)
    assert position.avg_cost == D("120.5")


def test_several_sells_consume_lots_in_order() -> None:
    txs = [
        Transaction.buy(at("2026-01-05"), "A", D(5), D(10), "EUR"),
        Transaction.buy(at("2026-01-06"), "A", D(5), D(20), "EUR"),
        Transaction.buy(at("2026-01-07"), "A", D(5), D(30), "EUR"),
        Transaction.sell(at("2026-02-01"), "A", D(3), D(40), "EUR"),
        Transaction.sell(at("2026-02-02"), "A", D(4), D(40), "EUR"),
    ]
    first, second = build_book(txs, AS_OF).realisations
    assert first.cost == D(30)  # 3 x 10
    assert second.cost == D(60)  # 2 x 10 + 2 x 20
    assert positions(txs, AS_OF)["A"].cost == D(210)  # 3 x 20 + 5 x 30


def test_split_between_buy_and_sell() -> None:
    txs = [
        Transaction.buy(at("2026-01-05"), "A", D(10), D(400), "USD"),
        Transaction.split(at("2026-02-02"), "A", D(4), "USD"),
        Transaction.sell(at("2026-03-02"), "A", D(20), D(110), "USD"),
    ]
    # Before the split the stored quantity is what was bought.
    assert positions(txs, d("2026-02-01"))["A"].qty == D(10)
    # After it, 4-for-1 at read time; the cost is unchanged.
    after_split = positions(txs, d("2026-02-02"))["A"]
    assert (after_split.qty, after_split.cost, after_split.avg_cost) == (D(40), D(4000), D(100))

    book = build_book(txs, AS_OF)
    [sale] = book.realisations
    assert (sale.cost, sale.proceeds, sale.pnl) == (D(2000), D(2200), D(200))
    assert book.positions["A"].qty == D(20)
    # The stored transactions are not touched.
    assert txs[0].qty == D(10)


def test_reverse_split() -> None:
    txs = [
        Transaction.buy(at("2026-01-05"), "A", D(100), D(1), "EUR"),
        Transaction.buy(at("2026-01-06"), "A", D(50), D(2), "EUR"),
        Transaction.split(at("2026-02-02"), "A", D("0.1"), "EUR"),
    ]
    position = positions(txs, AS_OF)["A"]
    assert position.qty == D(15)
    assert [lot.unit_cost for lot in position.lots] == [D(10), D(20)]
    assert position.cost == D(200)


def test_split_of_something_not_held_is_ignored() -> None:
    txs = [Transaction.split(at("2026-02-02"), "A", D(2), "EUR")]
    assert positions(txs, AS_OF) == {}


def test_selling_everything_closes_the_position() -> None:
    txs = [
        Transaction.buy(at("2026-01-05"), "A", D(10), D(1), "EUR"),
        Transaction.sell(at("2026-01-06"), "A", D(10), D(2), "EUR"),
    ]
    assert positions(txs, AS_OF) == {}


def test_oversell_is_an_error() -> None:
    txs = [
        Transaction.buy(at("2026-01-05"), "A", D(10), D(1), "EUR"),
        Transaction.sell(at("2026-01-06"), "A", D("10.0001"), D(2), "EUR"),
    ]
    with pytest.raises(OversellError, match=r"selling 10.0001 A .* only 10 held"):
        build_book(txs, AS_OF)


def test_selling_before_buying_is_an_oversell() -> None:
    txs = [
        Transaction.buy(at("2026-01-06"), "A", D(10), D(1), "EUR"),
        Transaction.sell(at("2026-01-05"), "A", D(1), D(2), "EUR"),
    ]
    with pytest.raises(OversellError):
        build_book(txs, AS_OF)


def test_input_order_does_not_matter_and_as_of_is_inclusive() -> None:
    buy = Transaction.buy(at("2026-01-05"), "A", D(10), D(1), "EUR")
    sell = Transaction.sell(at("2026-01-06", "23:59"), "A", D(4), D(2), "EUR")
    assert positions([sell, buy], d("2026-01-06"))["A"].qty == D(6)
    assert positions([sell, buy], d("2026-01-05"))["A"].qty == D(10)
    assert positions([sell, buy], d("2026-01-04")) == {}


def test_mixed_currencies_for_one_instrument_are_rejected() -> None:
    txs = [
        Transaction.buy(at("2026-01-05"), "A", D(1), D(1), "EUR"),
        Transaction.buy(at("2026-01-06"), "A", D(1), D(1), "USD"),
    ]
    with pytest.raises(InvalidTransaction, match="in USD, earlier transactions were in EUR"):
        build_book(txs, AS_OF)


def test_fractional_shares() -> None:
    txs = [
        Transaction.buy(at("2026-01-05"), "A", D("0.3333"), D(300), "EUR"),
        Transaction.sell(at("2026-01-06"), "A", D("0.1111"), D(330), "EUR"),
    ]
    book = build_book(txs, AS_OF)
    assert book.positions["A"].qty == D("0.2222")
    assert book.realisations[0].cost == D("33.33")
