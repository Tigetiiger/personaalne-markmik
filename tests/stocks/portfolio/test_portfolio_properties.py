"""Property tests over random valid transaction histories.

The generator keeps its own simple model of what is held, so a history never
oversells and the expected quantities are computed independently of the
FIFO code. Quantities stay short enough (trade sizes rounded to 4 dp) that
Decimal arithmetic on them is exact; money is compared with a tolerance.
"""

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import ROUND_DOWN, Decimal

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.util import TALLINN as TZ
from app.stocks.portfolio import (
    EntryKind,
    OversellError,
    Transaction,
    TxKind,
    build_book,
    contribution_ledger,
    pnl_summary,
)

CURRENCY = {"A": "EUR", "B": "USD"}
START = date(2026, 1, 1)
TOLERANCE = Decimal("1e-8")
QTY_STEP = Decimal("0.0001")
SPLIT_RATIOS = [Decimal(2), Decimal(3), Decimal(5), Decimal("0.5"), Decimal("0.2")]
# Ratios for which price / ratio is exact for a 2-dp price.
EXACT_SPLITS = [Decimal(2), Decimal(4), Decimal(5), Decimal(10), Decimal("0.5"), Decimal("0.1")]


def fx(on: date, from_ccy: str, to_ccy: str) -> Decimal:
    """Deterministic but date-dependent USD/EUR, so FX effects are non-zero."""
    eur_per_usd = Decimal("0.8") + Decimal(on.toordinal() % 20) / 100
    rates = {"EUR": Decimal(1), "USD": eur_per_usd}
    return rates[from_ccy] / rates[to_ccy]


def money(low: int, high: int) -> st.SearchStrategy[Decimal]:
    """low..high in cents."""
    return st.integers(low, high).map(lambda cents: Decimal(cents) / 100)


@dataclass(frozen=True)
class History:
    transactions: list[Transaction]
    held: dict[str, Decimal]
    end: date

    def later(self, days: int = 1) -> datetime:
        return datetime.combine(self.end + timedelta(days=days), time(12), TZ)


@st.composite
def histories(draw: st.DrawFn, income: bool = True, max_steps: int = 25) -> History:
    day = START
    stamp = datetime.combine(day, time(9), TZ)
    txs = [Transaction.deposit(stamp, draw(money(100_000, 10_000_000)))]
    held = {"A": Decimal(0), "B": Decimal(0)}
    for step in range(draw(st.integers(1, max_steps))):
        day += timedelta(days=draw(st.integers(0, 10)))
        ts = datetime.combine(day, time(10), TZ) + timedelta(seconds=step)
        ops = ["buy", "deposit", "withdrawal", "split"]
        if any(qty > 0 for qty in held.values()):
            ops.append("sell")
        if income:
            ops += ["dividend", "fee", "interest"]
        op = draw(st.sampled_from(ops))
        key = draw(st.sampled_from(sorted(CURRENCY)))
        ccy = CURRENCY[key]
        if op == "buy":
            qty = Decimal(draw(st.integers(1, 10_000))) / 100
            txs.append(
                Transaction.buy(ts, key, qty, draw(money(1, 100_000)), ccy, fee=draw(money(0, 500)))
            )
            held[key] += qty
        elif op == "sell":
            key = draw(st.sampled_from([k for k, qty in sorted(held.items()) if qty > 0]))
            fraction = Decimal(draw(st.integers(1, 100))) / 100
            qty = (held[key] * fraction).quantize(QTY_STEP, rounding=ROUND_DOWN)
            if draw(st.booleans()) or qty == 0:
                qty = held[key]
            txs.append(
                Transaction.sell(
                    ts, key, qty, draw(money(1, 100_000)), CURRENCY[key], fee=draw(money(0, 500))
                )
            )
            held[key] -= qty
        elif op == "split":
            ratio = draw(st.sampled_from(SPLIT_RATIOS))
            txs.append(Transaction.split(ts, key, ratio, ccy))
            held[key] *= ratio
        elif op == "dividend":
            gross = draw(money(1, 100_000))
            withheld = gross * Decimal(draw(st.integers(0, 30))) / 100
            txs.append(Transaction.dividend(ts, key, gross, ccy, tax_withheld=withheld))
        elif op == "interest":
            txs.append(Transaction.interest(ts, draw(money(1, 1000)), "EUR"))
        elif op == "fee":
            txs.append(Transaction.account_fee(ts, draw(money(1, 1000))))
        elif op == "deposit":
            txs.append(Transaction.deposit(ts, draw(money(1, 1_000_000)), ccy))
        else:
            txs.append(Transaction.withdrawal(ts, draw(money(1, 100_000)), ccy))
    # Shuffle the input: order must come from the timestamps alone.
    shuffled = draw(st.permutations(txs))
    return History(list(shuffled), held, day)


PROPERTY_SETTINGS = settings(
    max_examples=150, deadline=None, suppress_health_check=[HealthCheck.too_slow]
)


@PROPERTY_SETTINGS
@given(histories())
def test_quantity_is_conserved(history: History) -> None:
    book = build_book(history.transactions, history.end)
    got = {key: position.qty for key, position in book.positions.items()}
    expected = {key: qty for key, qty in history.held.items() if qty > 0}
    assert got == expected


@PROPERTY_SETTINGS
@given(histories(), st.data())
def test_total_return_decomposes(history: History, data: st.DataObject) -> None:
    prices = {key: data.draw(money(0, 100_000)) for key in CURRENCY}
    s = pnl_summary(history.transactions, history.end, prices, fx)
    parts = s.realised.eur + s.unrealised.eur + s.income - s.fees
    assert abs(s.total - parts) < TOLERANCE
    for pnl in (s.realised, s.unrealised):
        assert abs(pnl.eur - (pnl.price_effect_eur + pnl.fx_effect_eur)) < TOLERANCE


@PROPERTY_SETTINGS
@given(histories(income=False), st.data())
def test_realised_plus_unrealised_is_value_minus_net_invested(
    history: History, data: st.DataObject
) -> None:
    prices = {key: data.draw(money(0, 100_000)) for key in CURRENCY}
    s = pnl_summary(history.transactions, history.end, prices, fx)
    assert abs((s.realised.eur + s.unrealised.eur) - (s.value - s.net_invested)) < TOLERANCE


@PROPERTY_SETTINGS
@given(histories())
def test_fifo_only_sells_lots_that_were_bought(history: History) -> None:
    book = build_book(history.transactions, history.end)
    buys = [tx for tx in book.transactions if tx.kind is TxKind.BUY]
    for key in CURRENCY:
        bought_cost = sum((tx.gross + tx.fee for tx in buys if tx.instrument == key), Decimal(0))
        buy_dates = {tx.trade_date for tx in buys if tx.instrument == key}
        sales = [r for r in book.realisations if r.instrument == key]
        remaining = book.positions[key].lots if key in book.positions else ()
        consumed = [lot for sale in sales for lot in sale.lots]
        # Cost can only come from buys, and all of it is accounted for.
        total = sum((lot.cost for lot in [*consumed, *remaining]), Decimal(0))
        assert abs(total - bought_cost) < TOLERANCE
        for sale in sales:
            assert sum((lot.qty for lot in sale.lots), Decimal(0)) == sale.qty
            for lot in sale.lots:
                assert lot.qty > 0
                assert lot.opened in buy_dates
                assert lot.opened <= sale.sold
            # First in, first out: nothing left over is older than what was sold.
            newest_sold = max(lot.opened for lot in sale.lots)
            assert all(lot.opened >= newest_sold for lot in remaining if lot.opened <= sale.sold)


@PROPERTY_SETTINGS
@given(histories(), st.sampled_from(EXACT_SPLITS), st.data())
def test_splits_do_not_change_value(history: History, ratio: Decimal, data: st.DataObject) -> None:
    prices = {key: data.draw(money(0, 100_000)) for key in CURRENCY}
    after = history.end + timedelta(days=1)
    split_txs = [
        *history.transactions,
        *(Transaction.split(history.later(), key, ratio, ccy) for key, ccy in CURRENCY.items()),
    ]
    before = pnl_summary(history.transactions, after, prices, fx)
    split = pnl_summary(split_txs, after, {k: p / ratio for k, p in prices.items()}, fx)
    assert abs(before.value - split.value) < TOLERANCE
    assert abs(before.unrealised.eur - split.unrealised.eur) < TOLERANCE
    book_before = build_book(history.transactions, after)
    book_split = build_book(split_txs, after)
    for key, position in book_before.positions.items():
        assert book_split.positions[key].cost == position.cost
        assert book_split.positions[key].qty == position.qty * ratio


@PROPERTY_SETTINGS
@given(histories(), st.sampled_from(sorted(CURRENCY)))
def test_selling_more_than_held_is_rejected(history: History, key: str) -> None:
    oversell = Transaction.sell(
        history.later(), key, history.held[key] + QTY_STEP, Decimal(1), CURRENCY[key]
    )
    with pytest.raises(OversellError):
        build_book([*history.transactions, oversell], history.end + timedelta(days=1))


@PROPERTY_SETTINGS
@given(histories())
def test_contribution_balance_bookkeeping(history: History) -> None:
    entries = contribution_ledger(history.transactions, history.end, fx)
    contributed = sum((e.amount for e in entries if e.kind is EntryKind.CONTRIBUTION), Decimal(0))
    withdrawn = sum((e.amount for e in entries if e.kind is EntryKind.WITHDRAWAL), Decimal(0))
    taxable = sum((e.taxable for e in entries), Decimal(0))
    closing = entries[-1].carried_forward
    assert all(e.carried_forward >= 0 and e.taxable >= 0 for e in entries)
    assert all(e.taxable == 0 or e.carried_forward == 0 for e in entries)
    # Each taxed euro is one that took the balance below zero.
    assert abs((closing - taxable) - (contributed - withdrawn)) < TOLERANCE
