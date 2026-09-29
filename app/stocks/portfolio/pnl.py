"""Realised and unrealised P&L, in the instrument currency and in EUR.

EUR cost is converted at each lot's trade-date rate, EUR proceeds or market
value at the sale date or `as_of`. The EUR result splits exactly into

    price effect = local P&L x rate at the end
    FX effect    = local cost x (rate at the end) - cost in EUR at trade-date rates

Cash is tracked in EUR: there is no currency-conversion transaction kind, so
every cash flow is assumed converted at the ECB rate of its own date (the
broker auto-converts).
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .ledger import build_book, in_order
from .model import (
    BASE_CURRENCY,
    ZERO,
    FxLookup,
    Lot,
    PortfolioError,
    Position,
    Realisation,
    Transaction,
    TxKind,
    to_eur,
)


@dataclass(frozen=True, slots=True)
class PnL:
    local: Decimal
    eur: Decimal
    price_effect_eur: Decimal
    fx_effect_eur: Decimal

    def __add__(self, other: "PnL") -> "PnL":
        return PnL(
            self.local + other.local,
            self.eur + other.eur,
            self.price_effect_eur + other.price_effect_eur,
            self.fx_effect_eur + other.fx_effect_eur,
        )


NO_PNL = PnL(ZERO, ZERO, ZERO, ZERO)


def lots_cost_eur(lots: Iterable[Lot], currency: str, fx: FxLookup) -> Decimal:
    return sum((to_eur(lot.cost, lot.opened, currency, fx) for lot in lots), ZERO)


def _pnl(
    value: Decimal, cost: Decimal, cost_eur: Decimal, on: date, currency: str, fx: FxLookup
) -> PnL:
    rate = Decimal(1) if currency == BASE_CURRENCY else fx(on, currency, BASE_CURRENCY)
    local = value - cost
    return PnL(
        local=local,
        eur=value * rate - cost_eur,
        price_effect_eur=local * rate,
        fx_effect_eur=cost * rate - cost_eur,
    )


def realised_pnl(realisation: Realisation, fx: FxLookup) -> PnL:
    return _pnl(
        realisation.proceeds,
        realisation.cost,
        lots_cost_eur(realisation.lots, realisation.currency, fx),
        realisation.sold,
        realisation.currency,
        fx,
    )


def unrealised_pnl(position: Position, price: Decimal, as_of: date, fx: FxLookup) -> PnL:
    return _pnl(
        position.qty * price,
        position.cost,
        lots_cost_eur(position.lots, position.currency, fx),
        as_of,
        position.currency,
        fx,
    )


def market_value_eur(position: Position, price: Decimal, as_of: date, fx: FxLookup) -> Decimal:
    return to_eur(position.qty * price, as_of, position.currency, fx)


def price_for(prices: Mapping[str, Decimal], instrument: str) -> Decimal:
    try:
        price = prices[instrument]
    except KeyError:
        raise PortfolioError(f"no price for {instrument}") from None
    if price < 0:
        raise PortfolioError(f"negative price for {instrument}: {price}")
    return price


def cash_flow_eur(tx: Transaction, fx: FxLookup) -> Decimal:
    """The EUR effect of a transaction on the account's cash."""
    signed = {
        TxKind.DEPOSIT: tx.gross,
        TxKind.WITHDRAWAL: -tx.gross,
        TxKind.BUY: -(tx.gross + tx.fee),
        TxKind.SELL: tx.gross - tx.fee,
        TxKind.DIVIDEND: tx.net,
        TxKind.INTEREST: tx.net,
        TxKind.FEE: -tx.gross,
        TxKind.SPLIT: ZERO,
    }[tx.kind]
    return to_eur(signed, tx.trade_date, tx.currency, fx)


def cash_eur(transactions: Iterable[Transaction], as_of: date, fx: FxLookup) -> Decimal:
    return sum((cash_flow_eur(tx, fx) for tx in in_order(transactions, as_of)), ZERO)


def account_value_eur(
    transactions: Iterable[Transaction], as_of: date, prices: Mapping[str, Decimal], fx: FxLookup
) -> Decimal:
    """Cash plus holdings at `prices` (instrument currency, as of `as_of`)."""
    transactions = list(transactions)
    holdings = sum(
        (
            market_value_eur(position, price_for(prices, key), as_of, fx)
            for key, position in build_book(transactions, as_of).positions.items()
        ),
        ZERO,
    )
    return cash_eur(transactions, as_of, fx) + holdings


@dataclass(frozen=True, slots=True)
class PnLSummary:
    """Everything in EUR. `total` = value - net_invested
    = realised + unrealised + income - fees."""

    realised: PnL
    unrealised: PnL
    # Dividends and interest, net of withholding.
    income: Decimal
    # Standalone fees (commissions are in the trades' P&L).
    fees: Decimal
    net_invested: Decimal
    cash: Decimal
    holdings_value: Decimal

    @property
    def value(self) -> Decimal:
        return self.cash + self.holdings_value

    @property
    def total(self) -> Decimal:
        return self.value - self.net_invested


def pnl_summary(
    transactions: Iterable[Transaction], as_of: date, prices: Mapping[str, Decimal], fx: FxLookup
) -> PnLSummary:
    book = build_book(transactions, as_of)
    realised = sum((realised_pnl(r, fx) for r in book.realisations), NO_PNL)
    unrealised = NO_PNL
    holdings = ZERO
    for key, position in book.positions.items():
        price = price_for(prices, key)
        unrealised += unrealised_pnl(position, price, as_of, fx)
        holdings += market_value_eur(position, price, as_of, fx)

    def total_of(*kinds: TxKind) -> Decimal:
        return sum((cash_flow_eur(tx, fx) for tx in book.transactions if tx.kind in kinds), ZERO)

    return PnLSummary(
        realised=realised,
        unrealised=unrealised,
        income=total_of(TxKind.DIVIDEND, TxKind.INTEREST),
        fees=-total_of(TxKind.FEE),
        net_invested=total_of(TxKind.DEPOSIT, TxKind.WITHDRAWAL),
        cash=sum((cash_flow_eur(tx, fx) for tx in book.transactions), ZERO),
        holdings_value=holdings,
    )
