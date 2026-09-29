"""Portfolio maths: lots, cost basis, P&L, FX, returns, Estonian investment account.

Ported from stock-oracle nearly unchanged: pure functions over the frozen types in `model`; the
only I/O is in `repository` and `view` (sqlite3). See TAX_NOTES.md for the investment-account
rules. holdings() and position() are the interface the other stocks packages use
(docs/CONTRACTS.md, "Aktsiad").
"""

from .allocation import (
    Slice,
    allocate,
    by_currency,
    by_instrument,
    by_kind,
    holdings_eur,
)
from .dividends import Dividend, dividends, yield_on_cost
from .investment_account import (
    DEFAULT_TAX_RATES,
    DeferredTax,
    Entry,
    EntryKind,
    UnknownTaxYear,
    YearSummary,
    contribution_balance,
    contribution_ledger,
    deferred_tax,
    tax_rate,
    year_summary,
)
from .ledger import Book, build_book, positions
from .model import (
    BASE_CURRENCY,
    Account,
    AccountKind,
    FxLookup,
    Instrument,
    InstrumentKind,
    InvalidTransaction,
    Lot,
    OversellError,
    PortfolioError,
    Position,
    Realisation,
    Transaction,
    TxKind,
)
from .pnl import (
    PnL,
    PnLSummary,
    account_value_eur,
    cash_eur,
    pnl_summary,
    realised_pnl,
    unrealised_pnl,
)
from .returns import account_xirr, external_flows, twr, valuations, xirr
from .view import holdings, position

__all__ = [
    "BASE_CURRENCY",
    "DEFAULT_TAX_RATES",
    "Account",
    "AccountKind",
    "Book",
    "DeferredTax",
    "Dividend",
    "Entry",
    "EntryKind",
    "FxLookup",
    "Instrument",
    "InstrumentKind",
    "InvalidTransaction",
    "Lot",
    "OversellError",
    "PnL",
    "PnLSummary",
    "PortfolioError",
    "Position",
    "Realisation",
    "Slice",
    "Transaction",
    "TxKind",
    "UnknownTaxYear",
    "YearSummary",
    "account_value_eur",
    "account_xirr",
    "allocate",
    "build_book",
    "by_currency",
    "by_instrument",
    "by_kind",
    "cash_eur",
    "contribution_balance",
    "contribution_ledger",
    "deferred_tax",
    "dividends",
    "external_flows",
    "holdings",
    "holdings_eur",
    "pnl_summary",
    "position",
    "positions",
    "realised_pnl",
    "tax_rate",
    "twr",
    "unrealised_pnl",
    "valuations",
    "xirr",
    "year_summary",
    "yield_on_cost",
]
