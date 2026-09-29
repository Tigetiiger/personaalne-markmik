"""The add/edit transaction form: raw strings in, a validated transaction or per-field errors out
(ported from stock-oracle's web/tx_form.py; messages are Estonian).

Only the fields the chosen kind uses are read, so values left behind in hidden inputs (the form
shows fields per kind) never cause errors.
"""

from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal

from ..util import TALLINN
from .parsing import ParseError, parse_date, parse_decimal
from .portfolio import InvalidTransaction, PortfolioError, Transaction, TxKind, build_book
from .portfolio.messages import estonian
from .portfolio.repository import load_transactions

FORM_FIELDS = ("account_id", "kind", "date", "instrument_id", "qty", "price", "amount", "ratio", "currency", "fee",
               "tax_withheld", "note")
USES = {
    TxKind.BUY: ("instrument_id", "qty", "price", "fee"),
    TxKind.SELL: ("instrument_id", "qty", "price", "fee"),
    TxKind.DIVIDEND: ("instrument_id", "amount", "tax_withheld"),
    TxKind.INTEREST: ("amount", "tax_withheld"),
    TxKind.SPLIT: ("instrument_id", "ratio"),
    TxKind.DEPOSIT: ("amount",),
    TxKind.WITHDRAWAL: ("amount",),
    TxKind.FEE: ("amount", "instrument_id"),
}
REQUIRED = {"qty", "price", "amount", "ratio"}
NOTE_MAX = 500


@dataclass
class TxFormResult:
    values: dict
    errors: dict = field(default_factory=dict)
    account_id: int | None = None
    tx: Transaction | None = None

    @property
    def ok(self):
        return not self.errors and self.tx is not None

    @property
    def note(self):
        return self.values["note"] or None


def blank_values(account_id=None, day=""):
    values = dict.fromkeys(FORM_FIELDS, "")
    values.update(kind="buy", date=day)
    if account_id is not None:
        values["account_id"] = str(account_id)
    return values


def _text(value):
    return "" if value is None else f"{Decimal(value).normalize():f}"


def values_of(row):
    """A `portfolio_tx` row as the form's strings."""
    local = datetime.fromisoformat(row["ts"].replace("Z", "+00:00")).astimezone(TALLINN)
    stamp = local.date().isoformat()
    if local.time() != time(12, 0):
        stamp += f" {local:%H:%M}"
    return {
        "account_id": str(row["account_id"]),
        "kind": row["kind"],
        "date": stamp,
        "instrument_id": str(row["instrument_id"] or ""),
        "qty": _text(row["qty"]),
        "price": _text(row["price"]),
        "amount": _text(row["amount"]),
        "ratio": _text(row["ratio"]),
        "currency": row["currency"],
        "fee": _text(row["fee"]) if Decimal(row["fee"]) else "",
        "tax_withheld": _text(row["tax_withheld"]) if Decimal(row["tax_withheld"]) else "",
        "note": row["note"] or "",
    }


def validate(conn, form, editing=None):
    """`editing` is the `portfolio_tx` row being changed, or None for a new one."""
    values = {name: str(form.get(name) if form.get(name) is not None else "").strip() for name in FORM_FIELDS}
    result = TxFormResult(values)
    errors = result.errors

    try:
        kind = TxKind(values["kind"])
    except ValueError:
        errors["kind"] = "Vali tehingu liik."
        return result
    account = (conn.execute("SELECT * FROM broker_accounts WHERE id = ?", (int(values["account_id"]),)).fetchone()
               if values["account_id"].isdigit() else None)
    if account is None:
        errors["account_id"] = "Vali konto."

    try:
        day, clock = parse_date(values["date"])
        hour, minute = clock or (12, 0)
        ts = datetime.combine(day, time(hour, minute), tzinfo=TALLINN)
    except ParseError as exc:
        errors["date"] = str(exc)[:1].upper() + str(exc)[1:] + "."
        ts = None

    used = USES[kind]
    instrument = None
    if "instrument_id" in used:
        if values["instrument_id"].isdigit():
            instrument = conn.execute("SELECT * FROM instruments WHERE id = ? AND kind IN ('stock', 'etf')",
                                      (int(values["instrument_id"]),)).fetchone()
        if instrument is None and kind is not TxKind.FEE:
            errors["instrument_id"] = "Vali väärtpaber."

    numbers = {}
    for name in ("qty", "price", "amount", "ratio", "fee", "tax_withheld"):
        if name not in used:
            continue
        try:
            numbers[name] = parse_decimal(values[name])
        except ParseError:
            errors[name] = "Kirjuta arv, nt 12,50."
            continue
        if numbers[name] is None and name in REQUIRED:
            errors[name] = "Kohustuslik."
    if len(values["note"]) > NOTE_MAX:
        errors["note"] = f"Märkus on liiga pikk (kuni {NOTE_MAX} märki)."

    currency = values["currency"].upper() or (instrument["currency"] if instrument else "EUR")
    if errors or ts is None:
        return result

    symbols = {str(instrument["id"]): instrument["symbol"]} if instrument else {}
    try:
        tx = Transaction(
            kind,
            ts,
            currency,
            str(instrument["id"]) if instrument else None,
            qty=numbers.get("qty"),
            price=numbers.get("price"),
            amount=numbers.get("amount"),
            ratio=numbers.get("ratio"),
            fee=numbers.get("fee") or Decimal(0),
            tax_withheld=numbers.get("tax_withheld") or Decimal(0),
        )
    except InvalidTransaction as exc:
        errors["form"] = estonian(exc, symbols)
        return result

    exclude = editing["id"] if editing else None
    problem = replay_problem(conn, account["id"], [tx], exclude=exclude)
    if problem is None and editing and editing["account_id"] != account["id"]:
        # Moving a transaction to another account must leave the old one consistent too.
        problem = replay_problem(conn, editing["account_id"], [], exclude=exclude)
    if problem:
        errors["form"] = problem
        return result
    result.account_id, result.tx = account["id"], tx
    return result


def replay_problem(conn, account_id, added, exclude=None):
    """Why the account's history would stop making sense with `added` (and without the transaction
    `exclude`), or None. Catches selling more than held and switching an instrument's currency,
    also across accounts: the Portfell tab replays all of them as one book."""
    all_ids = [r["id"] for r in conn.execute("SELECT id FROM broker_accounts")]
    for history in (load_transactions(conn, [account_id], exclude) + list(added),
                    load_transactions(conn, all_ids, exclude) + list(added)):
        if not history:
            continue
        try:
            build_book(history, max(tx.trade_date for tx in history))
        except PortfolioError as exc:
            symbols = {str(r["id"]): r["symbol"] for r in conn.execute("SELECT id, symbol FROM instruments")}
            return f"Nii ei klapiks konto ajalugu. {estonian(exc, symbols)}"
    return None
