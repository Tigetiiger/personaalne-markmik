"""Maps `broker_accounts`, `portfolio_tx`, `instruments` and `fx_rates` rows to the portfolio
domain types. With `view.py`, the only I/O in the package.

Instrument keys in the domain are `str(instruments.id)`.
"""

import bisect
from datetime import date

from ...util import iso, parse_iso
from ..common import dec, dtext
from .model import (
    BASE_CURRENCY,
    Account,
    AccountKind,
    Instrument,
    InstrumentKind,
    PortfolioError,
    Transaction,
    TxKind,
)

_ACCOUNT_KINDS = {"brokerage": AccountKind.REGULAR, "investment_account": AccountKind.INVESTMENT_ACCOUNT}
_INSTRUMENT_KINDS = {"stock": InstrumentKind.STOCK, "etf": InstrumentKind.ETF}
COLUMNS = ("account_id", "instrument_id", "kind", "ts", "qty", "price", "amount", "ratio", "currency", "fee",
           "tax_withheld", "note", "external_ref")


def instrument_key(instrument_id):
    return str(instrument_id)


def load_account(conn, account_id):
    row = conn.execute("SELECT * FROM broker_accounts WHERE id = ?", (account_id,)).fetchone()
    if row is None:
        raise PortfolioError(f"no account {account_id}")
    return Account(row["name"], _ACCOUNT_KINDS[row["kind"]], row["base_currency"])


def to_domain(row):
    return Transaction(
        kind=TxKind(row["kind"]),
        ts=parse_iso(row["ts"]),
        currency=row["currency"],
        instrument=instrument_key(row["instrument_id"]) if row["instrument_id"] is not None else None,
        qty=dec(row["qty"]),
        price=dec(row["price"]),
        amount=dec(row["amount"]),
        ratio=dec(row["ratio"]),
        fee=dec(row["fee"]),
        tax_withheld=dec(row["tax_withheld"]),
        ref=row["external_ref"],
    )


def to_row(account_id, tx, note=None):
    """Column values for `portfolio_tx`, in COLUMNS order."""
    return (account_id, int(tx.instrument) if tx.instrument is not None else None, tx.kind.value, iso(tx.ts),
            dtext(tx.qty), dtext(tx.price), dtext(tx.amount), dtext(tx.ratio), tx.currency, dtext(tx.fee),
            dtext(tx.tax_withheld), note, tx.ref)


def insert(conn, values):
    cur = conn.execute(f"INSERT INTO portfolio_tx ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
                       values)
    return cur.lastrowid


def add_transactions(conn, account_id, txs, notes=None):
    notes = notes or [None] * len(txs)
    return [insert(conn, to_row(account_id, tx, note)) for tx, note in zip(txs, notes, strict=True)]


def load_transactions(conn, account_ids, exclude=None):
    """All transactions of the given accounts, oldest first (by ts, then as entered). For the
    investment account rules, pass every investment account together."""
    ids = list(account_ids)
    if not ids:
        return []
    rows = conn.execute(
        f"SELECT * FROM portfolio_tx WHERE account_id IN ({', '.join('?' * len(ids))}) AND id IS NOT ? "
        "ORDER BY ts, id", (*ids, exclude)).fetchall()
    return [to_domain(row) for row in rows]


def load_all(conn):
    return load_transactions(conn, [r["id"] for r in conn.execute("SELECT id FROM broker_accounts")])


def load_instruments(conn, keys):
    ids = [int(key) for key in keys]
    rows = conn.execute(f"SELECT id, symbol, currency, kind FROM instruments WHERE id IN ({', '.join('?' * len(ids))})",
                        ids).fetchall() if ids else []
    result = {}
    for row in rows:
        kind = _INSTRUMENT_KINDS.get(row["kind"])
        if kind is None:
            raise PortfolioError(f"instrument {row['symbol']} is an {row['kind']}, not holdable")
        result[instrument_key(row["id"])] = Instrument(instrument_key(row["id"]), row["currency"], kind)
    return result


class DbFx:
    """FX lookup over `fx_rates`, which holds ECB rates as 1 EUR = rate `quote`.

    Uses the latest rate on or before the date, since the ECB publishes none on weekends and
    TARGET holidays. Cross rates go through EUR. Each currency's rates are read once: the history
    chart asks for thousands of lookups.
    """

    def __init__(self, conn):
        self.conn = conn
        self.series = {}

    def eur_rate(self, on, currency):
        """Units of `currency` per 1 EUR."""
        if currency == BASE_CURRENCY:
            return dec(1)
        if currency not in self.series:
            rows = self.conn.execute("SELECT date, rate FROM fx_rates WHERE base = ? AND quote = ? ORDER BY date",
                                     (BASE_CURRENCY, currency)).fetchall()
            self.series[currency] = ([date.fromisoformat(r["date"]) for r in rows], [dec(r["rate"]) for r in rows])
        days, rates = self.series[currency]
        i = bisect.bisect_right(days, on)
        if not i:
            raise PortfolioError(f"EUR/{currency} kurssi pole {on.isoformat()} ega varasema päeva kohta")
        return rates[i - 1]

    def __call__(self, on, from_ccy, to_ccy):
        return self.eur_rate(on, to_ccy) / self.eur_rate(on, from_ccy)
