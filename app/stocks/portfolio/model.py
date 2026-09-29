"""Domain types for the portfolio maths.

Everything here is a frozen dataclass over Decimal. Transactions are stored
exactly as entered (stock-oracle PLAN.md: "never adjusted"); splits are applied when the
book is replayed, in `ledger.py`.

Conventions:
- Amounts are in the transaction's own currency; the base currency is EUR.
- A transaction's trade date is its timestamp's calendar date in Tallinn.
- FX is injected as `fx(on, from_ccy, to_ccy)`: 1 `from_ccy` = rate `to_ccy`.
"""

import enum
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

from ...util import TALLINN as TZ

BASE_CURRENCY = "EUR"
ZERO = Decimal(0)

FxLookup = Callable[[date, str, str], Decimal]

_CURRENCY = re.compile(r"^[A-Z]{3}$")


class PortfolioError(ValueError):
    pass


class InvalidTransaction(PortfolioError):
    pass


class OversellError(PortfolioError):
    pass


class TxKind(enum.StrEnum):
    BUY = "buy"
    SELL = "sell"
    DIVIDEND = "dividend"
    FEE = "fee"
    DEPOSIT = "deposit"
    WITHDRAWAL = "withdrawal"
    SPLIT = "split"
    INTEREST = "interest"


class AccountKind(enum.StrEnum):
    REGULAR = "regular"
    INVESTMENT_ACCOUNT = "investment_account"


class InstrumentKind(enum.StrEnum):
    STOCK = "stock"
    ETF = "etf"


@dataclass(frozen=True, slots=True)
class Account:
    name: str
    kind: AccountKind
    base_currency: str = BASE_CURRENCY

    def __post_init__(self) -> None:
        if self.base_currency != BASE_CURRENCY:
            raise PortfolioError(f"only EUR-based accounts are supported, got {self.base_currency}")


@dataclass(frozen=True, slots=True)
class Instrument:
    key: str
    currency: str
    kind: InstrumentKind


_TRADES = {TxKind.BUY, TxKind.SELL}
_NEEDS_INSTRUMENT = {TxKind.BUY, TxKind.SELL, TxKind.DIVIDEND, TxKind.SPLIT}
_NO_INSTRUMENT = {TxKind.DEPOSIT, TxKind.WITHDRAWAL, TxKind.INTEREST}
_CASH_AMOUNT = {TxKind.DIVIDEND, TxKind.DEPOSIT, TxKind.WITHDRAWAL, TxKind.FEE, TxKind.INTEREST}


@dataclass(frozen=True, slots=True)
class Transaction:
    """One transaction as entered.

    - buy/sell: `qty` shares at `price`; `fee` is the commission, in `currency`.
    - dividend/interest: `amount` is gross, `tax_withheld` what was withheld at source.
    - deposit/withdrawal/fee: `amount` is the positive cash amount.
    - split: `ratio` new shares per old share (4-for-1 = 4, 1-for-10 = 0.1).
    """

    kind: TxKind
    ts: datetime
    currency: str
    instrument: str | None = None
    qty: Decimal | None = None
    price: Decimal | None = None
    amount: Decimal | None = None
    ratio: Decimal | None = None
    fee: Decimal = ZERO
    tax_withheld: Decimal = ZERO
    ref: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        self._validate()

    def _fail(self, problem: str) -> InvalidTransaction:
        return InvalidTransaction(f"{self.kind} at {self.ts}: {problem}")

    def _validate(self) -> None:
        if self.ts.tzinfo is None or self.ts.utcoffset() is None:
            raise self._fail("timestamp must be timezone-aware")
        if not _CURRENCY.match(self.currency):
            raise self._fail(f"currency must be a 3-letter ISO code, got {self.currency!r}")
        for name in ("qty", "price", "amount", "ratio", "fee", "tax_withheld"):
            value = getattr(self, name)
            if value is None:
                continue
            if not isinstance(value, Decimal) or not value.is_finite():
                raise self._fail(f"{name} must be a finite Decimal, got {value!r}")
        if self.fee < 0 or self.tax_withheld < 0:
            raise self._fail("fee and tax_withheld must not be negative")

        if self.kind in _NEEDS_INSTRUMENT and not self.instrument:
            raise self._fail("instrument is required")
        if self.kind in _NO_INSTRUMENT and self.instrument is not None:
            raise self._fail("must not name an instrument")

        if self.kind in _TRADES:
            if self.qty is None or self.qty <= 0:
                raise self._fail("qty must be positive")
            if self.price is None or self.price < 0:
                raise self._fail("price must be zero or positive")
            if self.amount is not None or self.ratio is not None:
                raise self._fail("amount and ratio do not apply to trades")
        elif self.qty is not None or self.price is not None:
            raise self._fail("qty and price only apply to buy and sell")

        if self.kind is TxKind.SPLIT:
            if self.ratio is None or self.ratio <= 0:
                raise self._fail("ratio must be positive")
        elif self.ratio is not None:
            raise self._fail("ratio only applies to splits")

        if self.kind in _CASH_AMOUNT:
            if self.amount is None or self.amount <= 0:
                raise self._fail("amount must be positive")
        elif self.amount is not None:
            raise self._fail("amount does not apply")

        if self.kind in {TxKind.DIVIDEND, TxKind.INTEREST}:
            assert self.amount is not None
            if self.tax_withheld > self.amount:
                raise self._fail("tax_withheld exceeds the gross amount")
        elif self.tax_withheld:
            raise self._fail("tax_withheld only applies to dividends and interest")
        if self.fee and self.kind not in _TRADES:
            raise self._fail("fee only applies to buy and sell; record other fees as kind=fee")

    @property
    def trade_date(self) -> date:
        return self.ts.astimezone(TZ).date()

    @property
    def gross(self) -> Decimal:
        """Trade value (qty x price), or the cash amount for the other kinds."""
        if self.kind in _TRADES:
            assert self.qty is not None and self.price is not None
            return self.qty * self.price
        return self.amount if self.amount is not None else ZERO

    @property
    def net(self) -> Decimal:
        """Dividend/interest after withholding."""
        return self.gross - self.tax_withheld

    @classmethod
    def buy(
        cls,
        ts: datetime,
        instrument: str,
        qty: Decimal,
        price: Decimal,
        currency: str,
        fee: Decimal = ZERO,
        ref: str | None = None,
    ) -> "Transaction":
        return cls(TxKind.BUY, ts, currency, instrument, qty=qty, price=price, fee=fee, ref=ref)

    @classmethod
    def sell(
        cls,
        ts: datetime,
        instrument: str,
        qty: Decimal,
        price: Decimal,
        currency: str,
        fee: Decimal = ZERO,
        ref: str | None = None,
    ) -> "Transaction":
        return cls(TxKind.SELL, ts, currency, instrument, qty=qty, price=price, fee=fee, ref=ref)

    @classmethod
    def dividend(
        cls,
        ts: datetime,
        instrument: str,
        gross: Decimal,
        currency: str,
        tax_withheld: Decimal = ZERO,
        ref: str | None = None,
    ) -> "Transaction":
        return cls(
            TxKind.DIVIDEND,
            ts,
            currency,
            instrument,
            amount=gross,
            tax_withheld=tax_withheld,
            ref=ref,
        )

    @classmethod
    def interest(
        cls,
        ts: datetime,
        gross: Decimal,
        currency: str,
        tax_withheld: Decimal = ZERO,
        ref: str | None = None,
    ) -> "Transaction":
        return cls(TxKind.INTEREST, ts, currency, amount=gross, tax_withheld=tax_withheld, ref=ref)

    @classmethod
    def split(
        cls, ts: datetime, instrument: str, ratio: Decimal, currency: str, ref: str | None = None
    ) -> "Transaction":
        return cls(TxKind.SPLIT, ts, currency, instrument, ratio=ratio, ref=ref)

    @classmethod
    def deposit(
        cls, ts: datetime, amount: Decimal, currency: str = BASE_CURRENCY, ref: str | None = None
    ) -> "Transaction":
        return cls(TxKind.DEPOSIT, ts, currency, amount=amount, ref=ref)

    @classmethod
    def withdrawal(
        cls, ts: datetime, amount: Decimal, currency: str = BASE_CURRENCY, ref: str | None = None
    ) -> "Transaction":
        return cls(TxKind.WITHDRAWAL, ts, currency, amount=amount, ref=ref)

    @classmethod
    def account_fee(
        cls,
        ts: datetime,
        amount: Decimal,
        currency: str = BASE_CURRENCY,
        instrument: str | None = None,
        ref: str | None = None,
    ) -> "Transaction":
        return cls(TxKind.FEE, ts, currency, instrument, amount=amount, ref=ref)


@dataclass(frozen=True, slots=True)
class Lot:
    """Shares bought together. `cost` is the total, commission included, in the
    instrument currency; it is carried unchanged through splits."""

    instrument: str
    opened: date
    qty: Decimal
    cost: Decimal

    @property
    def unit_cost(self) -> Decimal:
        return self.cost / self.qty


@dataclass(frozen=True, slots=True)
class Position:
    instrument: str
    currency: str
    lots: tuple[Lot, ...]

    @property
    def qty(self) -> Decimal:
        return sum((lot.qty for lot in self.lots), ZERO)

    @property
    def cost(self) -> Decimal:
        return sum((lot.cost for lot in self.lots), ZERO)

    @property
    def avg_cost(self) -> Decimal:
        return self.cost / self.qty


@dataclass(frozen=True, slots=True)
class Realisation:
    """A sale matched FIFO against the lots it consumed."""

    instrument: str
    currency: str
    sold: date
    qty: Decimal
    # Net of the commission.
    proceeds: Decimal
    lots: tuple[Lot, ...]

    @property
    def cost(self) -> Decimal:
        return sum((lot.cost for lot in self.lots), ZERO)

    @property
    def pnl(self) -> Decimal:
        return self.proceeds - self.cost


def convert(amount: Decimal, on: date, from_ccy: str, to_ccy: str, fx: FxLookup) -> Decimal:
    if from_ccy == to_ccy:
        return amount
    return amount * fx(on, from_ccy, to_ccy)


def to_eur(amount: Decimal, on: date, currency: str, fx: FxLookup) -> Decimal:
    return convert(amount, on, currency, BASE_CURRENCY, fx)
