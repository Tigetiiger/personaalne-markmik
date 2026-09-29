"""Aktsiad: the portfolio package's JSON API (docs/CONTRACTS.md, "Aktsiad", stocks-portfolio):
accounts, transactions with the replay check, the CSV import, the Portfell tab's numbers and a
stock's position. Owner only (default-deny in auth), Estonian errors, Decimals as strings."""

import os
import sqlite3
from datetime import date
from decimal import Decimal

from flask import Blueprint, jsonify, request

from ..db import get_db, tx
from ..stocks import csv_import, portfolio, tx_form
from ..stocks.common import by_symbol, dtext
from ..stocks.config import SYMBOL
from ..stocks.portfolio import TxKind, view
from ..stocks.portfolio.repository import COLUMNS, insert, to_row
from ..util import local_today

bp = Blueprint("stocks_portfolio", __name__)

SHOWN = 500
MAX_UPLOAD_BYTES = 2_000_000
ACCOUNT_KINDS = ("brokerage", "investment_account")
ENCODINGS = ("utf-8-sig", "cp1257", "latin-1")


def _bad(message, status=400, **extra):
    return jsonify(error=message, **extra), status


def plain(value):
    """Decimals as plain strings ("10", never "1E+1"), dates as 'YYYY-MM-DD', recursively."""
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {k: plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [plain(v) for v in value]
    return value


# --- Accounts --------------------------------------------------------------------------------------

def _accounts(conn):
    return [dict(r) for r in conn.execute(
        "SELECT a.id, a.name, a.broker, a.kind, a.base_currency, "
        "(SELECT COUNT(*) FROM portfolio_tx t WHERE t.account_id = a.id) AS tx_count "
        "FROM broker_accounts a ORDER BY a.name")]


@bp.get("/api/stocks/accounts")
def accounts():
    return jsonify(_accounts(get_db()))


@bp.post("/api/stocks/accounts")
def add_account():
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _bad("Vigane päring.")
    name, broker = str(data.get("name") or "").strip(), str(data.get("broker") or "LHV").strip() or "LHV"
    if not name or len(name) > 80 or len(broker) > 80:
        return _bad("Kontol peab olema nimi (kuni 80 märki).")
    if data.get("kind") not in ACCOUNT_KINDS:
        return _bad("Vali konto liik.")
    conn = get_db()
    try:
        cur = conn.execute("INSERT INTO broker_accounts (name, broker, kind) VALUES (?, ?, ?)", (name, broker, data["kind"]))
    except sqlite3.IntegrityError:
        return _bad("Sellise nimega konto on juba olemas.", 409)
    return jsonify(next(a for a in _accounts(conn) if a["id"] == cur.lastrowid)), 201


# --- Transactions ----------------------------------------------------------------------------------

def _rows(conn, tx_id=None):
    where = "WHERE t.id = ?" if tx_id is not None else ""
    rows = conn.execute(
        f"SELECT t.*, a.name AS account, i.symbol FROM portfolio_tx t JOIN broker_accounts a ON a.id = t.account_id "
        f"LEFT JOIN instruments i ON i.id = t.instrument_id {where} ORDER BY t.ts DESC, t.id DESC LIMIT {SHOWN}",
        (tx_id,) if tx_id is not None else ()).fetchall()
    return [{"id": r["id"], "account": r["account"], "symbol": r["symbol"], "ts": r["ts"],
             "external_ref": r["external_ref"], "values": tx_form.values_of(r)} for r in rows]


def _instruments(conn):
    return [dict(r) for r in conn.execute(
        "SELECT id, symbol, name, currency, kind FROM instruments WHERE kind IN ('stock', 'etf') ORDER BY symbol, exchange")]


def _watch_if_bought(conn, kind, instrument_id):
    """Holdings are always on the watchlist."""
    if kind == TxKind.BUY and instrument_id is not None:
        conn.execute("INSERT OR IGNORE INTO watchlist (instrument_id) VALUES (?)", (instrument_id,))


def _get_tx(conn, tx_id):
    return conn.execute("SELECT * FROM portfolio_tx WHERE id = ?", (tx_id,)).fetchone()


@bp.get("/api/stocks/tx")
def transactions():
    conn = get_db()
    return jsonify(rows=_rows(conn), accounts=_accounts(conn), instruments=_instruments(conn),
                   today=local_today().isoformat(), uses={k.value: list(v) for k, v in tx_form.USES.items()},
                   shown=SHOWN)


def _save(conn, editing=None):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _bad("Vigane päring.")
    with tx(conn):
        result = tx_form.validate(conn, data, editing=editing)
        if not result.ok:
            return _bad(result.errors.get("form") or "Kontrolli välju.", errors=result.errors)
        values = to_row(result.account_id, result.tx, result.note)
        if editing is None:
            tx_id = insert(conn, values)
        else:
            tx_id = editing["id"]
            # external_ref stays: it is what makes a re-imported CSV row skip.
            columns = COLUMNS[:-1]
            conn.execute(f"UPDATE portfolio_tx SET {', '.join(f'{c} = ?' for c in columns)} WHERE id = ?",
                         (*values[:-1], tx_id))
        _watch_if_bought(conn, result.tx.kind, values[1])
    return jsonify(_rows(conn, tx_id)[0]), 201 if editing is None else 200


@bp.post("/api/stocks/tx")
def add_tx():
    return _save(get_db())


@bp.put("/api/stocks/tx/<int:tx_id>")
def edit_tx(tx_id):
    conn = get_db()
    row = _get_tx(conn, tx_id)
    if row is None:
        return _bad("Tehingut ei leitud.", 404)
    return _save(conn, editing=row)


@bp.delete("/api/stocks/tx/<int:tx_id>")
def delete_tx(tx_id):
    conn = get_db()
    with tx(conn):
        row = _get_tx(conn, tx_id)
        if row is None:
            return _bad("Tehingut ei leitud.", 404)
        problem = tx_form.replay_problem(conn, row["account_id"], [], exclude=tx_id)
        if problem:
            return _bad(f"Ei kustutatud. {problem}", 409)
        conn.execute("DELETE FROM portfolio_tx WHERE id = ?", (tx_id,))
    return "", 204


# --- CSV import ------------------------------------------------------------------------------------

class TooLarge(Exception):
    pass


def _read_upload():
    """(fields, csv text) from a multipart upload (`file`) or JSON {csv_text, account_id, mapping}."""
    if request.files.get("file"):
        data = request.files["file"].read(MAX_UPLOAD_BYTES + 1)
        if len(data) > MAX_UPLOAD_BYTES:
            raise TooLarge
        for encoding in ENCODINGS:
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        fields = request.form.to_dict()
        mapping = {k[4:]: v for k, v in fields.items() if k.startswith("map_")}
        return fields.get("account_id"), mapping, text
    data = request.get_json(silent=True) or {}
    text = data.get("csv_text") if isinstance(data.get("csv_text"), str) else ""
    if len(text.encode()) > MAX_UPLOAD_BYTES:
        raise TooLarge
    mapping = data.get("mapping") if isinstance(data.get("mapping"), dict) else {}
    return data.get("account_id"), {str(k): str(v) for k, v in mapping.items()}, text


def _account(conn, value):
    if isinstance(value, int) or (isinstance(value, str) and value.isdigit()):
        return conn.execute("SELECT * FROM broker_accounts WHERE id = ?", (int(value),)).fetchone()
    return None


def _resolver(conn):
    def resolve(symbol, isin):
        conditions, params = [], []
        if symbol:
            conditions.append("UPPER(symbol) = ?")
            params.append(symbol.strip().upper())
        if isin:
            conditions.append("isin = ?")
            params.append(isin.strip().upper())
        if not conditions:
            return None
        found = conn.execute(f"SELECT id, currency FROM instruments WHERE kind IN ('stock', 'etf') AND "
                             f"({' OR '.join(conditions)}) ORDER BY id LIMIT 1", params).fetchone()
        return (found["id"], found["currency"]) if found else None
    return resolve


def _existing_refs(conn, account, preview):
    refs = [r.tx.ref for r in preview.rows if r.tx is not None and r.tx.ref]
    if account is None or not refs:
        return set()
    found = set()
    for i in range(0, len(refs), 500):
        chunk = refs[i:i + 500]
        found |= {r[0] for r in conn.execute(
            f"SELECT external_ref FROM portfolio_tx WHERE account_id = ? AND external_ref IN ({', '.join('?' * len(chunk))})",
            (account["id"], *chunk))}
    for r in preview.rows:
        if r.tx is not None and r.tx.ref in found:
            r.duplicate = True
    return found


def _preview(conn):
    account_id, mapping, text = _read_upload()
    account = _account(conn, account_id)
    preview = csv_import.parse_csv(text, _resolver(conn), mapping) if text.strip() else None
    existing = _existing_refs(conn, account, preview) if preview else set()
    return account, preview, existing, text


def _preview_json(preview, existing, text):
    rows = []
    for r in preview.rows:
        t = r.tx
        if r.errors:
            status = "; ".join(r.errors)
        elif r.duplicate:
            status = "juba imporditud" if t and t.ref in existing else "kordub failis"
        else:
            status = "valmis"
        rows.append({
            "line": r.line, "kind": r.kind.value if r.kind else None, "symbol": r.symbol,
            "date": t.trade_date if t else None, "qty": t.qty if t else None, "price": t.price if t else None,
            "amount": t.amount if t else None, "ratio": t.ratio if t else None, "currency": t.currency if t else None,
            "fee": t.fee if t else None, "tax": t.tax_withheld if t else None, "ref": t.ref if t else None,
            "note": r.note, "errors": r.errors, "duplicate": r.duplicate, "importable": r.importable, "status": status,
        })
    return plain({
        "headers": preview.headers, "columns": preview.columns, "delimiter": preview.delimiter,
        "decimal_mark": preview.decimal_mark, "unmapped": preview.unmapped, "errors": preview.errors,
        "fields": list(csv_import.FIELDS), "labels": csv_import.LABELS, "rows": rows,
        "ready": len(preview.importable), "total": len(preview.rows), "csv_text": text,
    })


@bp.post("/api/stocks/import/preview")
def import_preview():
    conn = get_db()
    try:
        _, preview, existing, text = _preview(conn)
    except TooLarge:
        return _bad("Fail on suurem kui 2 MB.", 413)
    if preview is None:
        return _bad("Vali CSV-fail.")
    return jsonify(_preview_json(preview, existing, text))


@bp.post("/api/stocks/import/confirm")
def import_confirm():
    conn = get_db()
    try:
        with tx(conn):
            account, preview, _, _ = _preview(conn)
            if preview is None:
                return _bad("Vali CSV-fail.")
            if account is None:
                return _bad("Vali konto, kuhu importida.")
            rows = preview.importable
            if not rows:
                return jsonify(imported=0, skipped=len(preview.rows), account=account["name"])
            problem = tx_form.replay_problem(conn, account["id"], [r.tx for r in rows])
            if problem:
                return _bad(problem, 409)
            for r in rows:
                insert(conn, to_row(account["id"], r.tx, r.note))
                _watch_if_bought(conn, r.tx.kind, r.instrument_id)
    except TooLarge:
        return _bad("Fail on suurem kui 2 MB.", 413)
    except sqlite3.IntegrityError:
        return _bad("Import põrkas olemasolevate ridadega kokku; midagi ei salvestatud.", 409)
    return jsonify(imported=len(rows), skipped=len(preview.rows) - len(rows), account=account["name"])


# --- The Portfell tab and a stock's position ------------------------------------------------------

@bp.get("/api/stocks/portfolio")
def summary():
    return jsonify(plain(view.build(get_db(), local_today())))


@bp.get("/api/stocks/position/<symbol>")
def position(symbol):
    """The owner's position in one stock, or null when not held."""
    if not SYMBOL.fullmatch(symbol):
        return _bad("Tundmatu sümbol.", 404)
    conn = get_db()
    row = by_symbol(conn, symbol)
    if row is None:
        return jsonify(None)
    held = portfolio.position(conn, row["id"], local_today())
    return jsonify(plain({**held, "symbol": row["symbol"]}) if held else None)


# --- Browser tests (MARKMIK_TEST_PAGES=1 only) ------------------------------------------------------

if os.environ.get("MARKMIK_TEST_PAGES") == "1":
    @bp.post("/_test/stocks-portfolio/seed")
    def test_seed():
        """The market package's tables, which the public API cannot fill: {reset?, instruments:
        [{symbol, name, currency, kind, exchange?}], bars: {symbol: [[date, close], ...]},
        fx: [[date, quote, rate]]}. reset drops every account and transaction first."""
        data = request.get_json(silent=True) or {}
        conn = get_db()
        ids = {}
        with tx(conn):
            if data.get("reset"):
                conn.execute("DELETE FROM portfolio_tx")
                conn.execute("DELETE FROM broker_accounts")
            for i in data.get("instruments", []):
                exchange = i.get("exchange", "TEST")
                conn.execute("INSERT OR IGNORE INTO instruments (symbol, exchange, currency, name, kind) VALUES (?, ?, ?, ?, ?)",
                             (i["symbol"], exchange, i["currency"], i["name"], i["kind"]))
                ids[i["symbol"]] = conn.execute("SELECT id FROM instruments WHERE symbol = ? AND exchange = ?",
                                                (i["symbol"], exchange)).fetchone()["id"]
            for symbol, bars in data.get("bars", {}).items():
                for day, close in bars:
                    conn.execute("INSERT OR REPLACE INTO price_bars (instrument_id, date, close, adj_close, source) "
                                 "VALUES (?, ?, ?, ?, 'test')", (ids[symbol], day, dtext(str(close)), dtext(str(close))))
            for day, quote, rate in data.get("fx", []):
                conn.execute("INSERT OR REPLACE INTO fx_rates (date, base, quote, rate) VALUES (?, 'EUR', ?, ?)",
                             (day, quote, dtext(str(rate))))
        return jsonify(ids=ids), 201
