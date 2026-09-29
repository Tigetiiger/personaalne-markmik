"""Moves stock-oracle's data into the stocks tables (docs/AKTSIAD-PLAN.md, "Step 7").

Export, read-only, from the running stock-oracle `db` container (one `<table>.json` per table):

    uv run python -m app.stocks.import_oracle --export data/import/oracle

which runs, per table, the same as this by hand:

    docker compose -p stock-oracle exec -T -e PGOPTIONS='-c default_transaction_read_only=on' db \\
      sh -c 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -c "$0"' \\
      "SELECT coalesce(json_agg(t), '[]') FROM \\"<table>\\" t" > data/import/oracle/<table>.json

Tables are exported one statement at a time, not in one snapshot: at cutover stock-oracle's
worker is stopped first, so nothing writes in between.

Import (DATABASE_PATH, else data/markmik.db; the schema is applied first):

    uv run python -m app.stocks.import_oracle data/import/oracle [--db PATH] [--replace]

Everything goes in one transaction, ids kept. It refuses when any stocks table already has rows;
--replace empties the tables it fills first (quotes go with their instruments by cascade;
stock_runs and stock_ai_runs stay). References to stock-oracle's claude_run and nightly_run
become NULL, since those tables are not carried over.
"""

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

from ..db import connect, init_db, tx
from ..util import iso
from .common import dtext


class ImportProblem(Exception):
    """The import cannot go ahead; the message says why."""


def _ts(value):
    """timestamptz as json_agg prints it ('2026-09-21T18:03:59.010498+03:00') -> UTC '...Z'."""
    if value is None:
        return None
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError(f"timestamp without a zone: {value!r}")
    return iso(dt)


def _day(value):
    return None if value is None else date.fromisoformat(value).isoformat()


def _js(value, empty):
    # Numbers inside JSONB arrive as Decimal (parse_float); the JSON columns are read back with a
    # plain json.loads, so float is what they would hold anyway.
    return json.dumps(empty if value is None else value, ensure_ascii=False, default=float)


def _source(value):
    # stock-oracle fetched Yahoo's data through yfinance; Märkmik calls the same source "yahoo".
    return "yahoo" if value == "yfinance" else value


def _provider_symbols(value):
    symbols = dict(value or {})
    if "yfinance" in symbols:
        symbols.setdefault("yahoo", symbols.pop("yfinance"))
    return _js(symbols, {})


def _cursor(value):
    """source_run.cursor: its timestamps ({"since", "until"}) become '...Z' like every other."""
    if not isinstance(value, dict):
        return None if value is None else _js(value, None)
    out = {}
    for key, v in value.items():
        if isinstance(v, str):
            try:
                dt = datetime.fromisoformat(v)
            except ValueError:
                dt = None
            if dt is not None and dt.tzinfo is not None:
                v = iso(dt)
        out[key] = v
    return _js(out, {})


def _count(value):
    n = int(value)
    if n != value:
        raise ValueError(f"mentions is not a whole number: {value}")
    return n


def _instrument(r):
    return {"id": r["id"], "symbol": r["symbol"], "exchange": r["exchange"], "currency": r["currency"],
            "name": r["name"], "kind": r["kind"], "isin": r["isin"], "sector": r["sector"],
            "provider_symbols_json": _provider_symbols(r["provider_symbols"]), "created_at": _ts(r["created_at"])}


def _watchlist(r):
    return {"instrument_id": r["instrument_id"], "added_at": _ts(r["added_at"]), "note": r["note"],
            "tags_json": _js(r["tags"], [])}


def _account(r):
    return {"id": r["id"], "name": r["name"], "broker": r["broker"], "base_currency": r["base_currency"],
            "kind": r["kind"], "created_at": _ts(r["created_at"])}


def _transaction(r):
    return {"id": r["id"], "account_id": r["account_id"], "instrument_id": r["instrument_id"], "kind": r["kind"],
            "ts": _ts(r["ts"]), "qty": dtext(r["qty"]), "price": dtext(r["price"]), "amount": dtext(r["amount"]),
            "ratio": dtext(r["ratio"]), "currency": r["currency"], "fee": dtext(r["fee"]),
            "tax_withheld": dtext(r["tax_withheld"]), "note": r["note"], "external_ref": r["external_ref"],
            "created_at": _ts(r["created_at"])}


def _price_bar(r):
    return {"id": r["id"], "instrument_id": r["instrument_id"], "date": _day(r["date"]), "open": dtext(r["open"]),
            "high": dtext(r["high"]), "low": dtext(r["low"]), "close": dtext(r["close"]),
            "adj_close": dtext(r["adj_close"]), "volume": r["volume"], "source": _source(r["source"]),
            "fetched_at": _ts(r["fetched_at"])}


def _corporate_action(r):
    # The source is renamed here too: split and dividend rows are unique per source, and market's
    # refresh writes "yahoo", so a kept "yfinance" split would be applied twice.
    return {"id": r["id"], "instrument_id": r["instrument_id"], "date": _day(r["date"]), "kind": r["kind"],
            "ratio": dtext(r["ratio"]), "amount": dtext(r["amount"]), "currency": r["currency"],
            "source": _source(r["source"])}


def _fx_rate(r):
    return {"date": _day(r["date"]), "base": r["base"], "quote": r["quote"], "rate": dtext(r["rate"])}


def _document(r):
    # search_vector (the tsvector) is left behind: only stock-oracle's MCP server searched it.
    return {"id": r["id"], "source": r["source"], "external_id": r["external_id"], "url": r["url"],
            "url_hash": r["url_hash"], "content_hash": r["content_hash"], "title": r["title"], "body": r["body"],
            "author": r["author"], "language": r["language"], "published_at": _ts(r["published_at"]),
            "fetched_at": _ts(r["fetched_at"]), "meta_json": _js(r["meta"], {})}


def _document_instrument(r):
    return {"document_id": r["document_id"], "instrument_id": r["instrument_id"]}


def _doc_signal(r):
    return {"id": r["id"], "document_id": r["document_id"], "instrument_id": r["instrument_id"],
            "sentiment": dtext(r["sentiment"]), "event_type": r["event_type"], "novelty": dtext(r["novelty"]),
            "relevance": dtext(r["relevance"]), "summary": r["summary"], "key_facts_json": _js(r["key_facts"], []),
            "prompt_version": r["prompt_version"], "created_at": _ts(r["created_at"])}


def _insider_trade(r):
    return {"id": r["id"], "instrument_id": r["instrument_id"], "document_id": r["document_id"],
            "insider_name": r["insider_name"], "insider_role": r["insider_role"], "trade_date": _day(r["trade_date"]),
            "kind": r["kind"], "code": r["code"], "qty": dtext(r["qty"]), "price": dtext(r["price"]),
            "shares_after": dtext(r["shares_after"])}


def _earnings_event(r):
    return {"id": r["id"], "instrument_id": r["instrument_id"], "date": _day(r["date"]),
            "time_of_day": r["time_of_day"], "fiscal_period": r["fiscal_period"],
            "eps_estimate": dtext(r["eps_estimate"]), "eps_actual": dtext(r["eps_actual"]),
            "revenue_estimate": dtext(r["revenue_estimate"]), "revenue_actual": dtext(r["revenue_actual"]),
            "source": r["source"], "updated_at": _ts(r["updated_at"])}


def _daily_metric(r):
    # Only the document counts come over (the attention alert's input); GDELT tone and the stored
    # z-scores are recomputed or gone.
    if r["metric"] != "mentions":
        return None
    return {"instrument_id": r["instrument_id"], "date": _day(r["date"]), "source": r["source"],
            "count": _count(r["value"])}


def _analysis(r):
    return {"id": r["id"], "instrument_id": r["instrument_id"], "kind": r["kind"], "as_of": _day(r["as_of"]),
            "summary": r["summary"], "what_changed": r["what_changed"], "bull_json": _js(r["bull"], []),
            "bear_json": _js(r["bear"], []), "catalysts_json": _js(r["catalysts"], []),
            "risks_json": _js(r["risks"], []), "change_my_mind_json": _js(r["what_would_change_my_mind"], []),
            "citations_json": _js(r["citations"], []), "prompt_version": r["prompt_version"], "model": r["model"],
            "created_at": _ts(r["created_at"])}


def _verdict(r):
    return {"id": r["id"], "instrument_id": r["instrument_id"], "as_of": _day(r["as_of"]), "verdict": r["verdict"],
            "conviction": r["conviction"], "summary": r["summary"], "reasons_for_json": _js(r["reasons_for"], []),
            "reasons_against_json": _js(r["reasons_against"], []),
            "what_would_change_json": _js(r["what_would_change"], []), "inputs_json": _js(r["inputs"], {}),
            "prompt_version": r["prompt_version"], "model": r["model"], "created_at": _ts(r["created_at"])}


def _exploration(r):
    return {"id": r["id"], "focus": r["focus"], "requested_ideas": r["requested_ideas"],
            "market_context": r["market_context"], "ideas_json": _js(r["ideas"], []),
            "dropped_json": _js(r["dropped"], []), "prompt_version": r["prompt_version"], "model": r["model"],
            "created_at": _ts(r["created_at"])}


def _source_run(r):
    return {"id": r["id"], "source": r["source"], "started_at": _ts(r["started_at"]),
            "finished_at": _ts(r["finished_at"]), "status": r["status"], "items_fetched": r["items_fetched"],
            "items_new": r["items_new"], "cursor_json": _cursor(r["cursor"]), "error": r["error"]}


def _notification(r):
    # Every row, sent or not: the dedupe keys are what stop an alert that already went out.
    return {"id": r["id"], "dedupe_key": r["dedupe_key"], "kind": r["kind"], "title": r["title"],
            "body": r["body"], "created_at": _ts(r["created_at"]), "sent_at": _ts(r["sent_at"]),
            "error": r["error"]}


# (stock-oracle table, Märkmik table, row -> columns or None to skip), parents before children.
IMPORTS = [
    ("instrument", "instruments", _instrument),
    ("watchlist_item", "watchlist", _watchlist),
    ("account", "broker_accounts", _account),
    ("transaction", "portfolio_tx", _transaction),
    ("price_bar", "price_bars", _price_bar),
    ("corporate_action", "corporate_actions", _corporate_action),
    ("fx_rate", "fx_rates", _fx_rate),
    ("document", "market_docs", _document),
    ("document_instrument", "market_doc_instruments", _document_instrument),
    ("doc_signal", "doc_signals", _doc_signal),
    ("insider_trade", "insider_trades", _insider_trade),
    ("earnings_event", "earnings", _earnings_event),
    ("daily_metric", "doc_counts", _daily_metric),
    ("analysis", "analyses", _analysis),
    ("verdict", "verdicts", _verdict),
    ("exploration", "explorations", _exploration),
    ("source_run", "stock_source_runs", _source_run),
    ("notification", "stock_notices", _notification),
]
SKIPPED = ("prediction", "quote", "fundamental_fact", "source_budget", "nightly_run", "claude_run",
           "alembic_version")
# Every stocks table, for the refusal; the ones not filled here are Märkmik's own.
STOCK_TABLES = [target for _, target, _ in IMPORTS] + ["quotes", "stock_runs", "stock_ai_runs"]


def _load(path):
    # parse_float=Decimal: a Numeric never passes through float on its way to a TEXT column.
    try:
        rows = json.loads(path.read_text(encoding="utf-8"), parse_float=Decimal)
    except ValueError as exc:
        raise ImportProblem(f"{path.name}: not valid JSON ({exc})") from exc
    if not isinstance(rows, list) or not all(isinstance(r, dict) for r in rows):
        raise ImportProblem(f"{path.name}: not a JSON array of row objects")
    return rows


def _ident(row):
    if "id" in row:
        return f"id {row['id']}"
    return ", ".join(f"{k}={row[k]}" for k in list(row)[:3])


def run(conn, folder, *, replace=False):
    """Import the JSON files in `folder`. Returns the summary: [(source, target, read, written,
    skipped)], with target None for a table skipped on purpose and "?" for an unknown file.
    Raises ImportProblem and leaves the database as it was."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ImportProblem(f"{folder}: no such folder")
    files = {p.name: p for p in folder.iterdir() if p.is_file()}
    wanted = {f"{source}.json" for source, _, _ in IMPORTS}
    missing = sorted(wanted - files.keys())
    if missing:
        raise ImportProblem(f"missing from {folder}: {', '.join(missing)} (an incomplete export)")
    data = {source: _load(files[f"{source}.json"]) for source, _, _ in IMPORTS}
    others = []
    for name in sorted(files.keys() - wanted):
        table = name.removesuffix(".json")
        if name.endswith(".json") and table in SKIPPED:
            n = len(_load(files[name]))
            others.append((table, None, n, 0, n))
        else:
            others.append((name, "?", 0, 0, 0))

    summary = []
    with tx(conn):
        filled = {t: conn.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in STOCK_TABLES}
        filled = {t: n for t, n in filled.items() if n}
        if filled and not replace:
            raise ImportProblem("the stocks tables already hold data ("
                                + ", ".join(f"{t} {n}" for t, n in filled.items())
                                + "); run with --replace to empty them first")
        if replace:
            for _, target, _ in reversed(IMPORTS):
                conn.execute(f"DELETE FROM {target}")
        for source, target, convert in IMPORTS:
            written = 0
            for row in data[source]:
                try:
                    values = convert(row)
                    if values is None:
                        continue
                    cols = ", ".join(values)
                    marks = ", ".join("?" * len(values))
                    conn.execute(f"INSERT INTO {target} ({cols}) VALUES ({marks})", tuple(values.values()))
                except (KeyError, ValueError, TypeError, sqlite3.IntegrityError) as exc:
                    raise ImportProblem(f"{source} {_ident(row)} -> {target}: {exc!r}") from exc
                written += 1
            summary.append((source, target, len(data[source]), written, len(data[source]) - written))
    return summary + others


def format_summary(summary):
    lines = [f"{'stock-oracle':<22}{'Märkmik':<26}{'read':>8}{'written':>9}{'skipped':>9}"]
    for source, target, read, written, skipped in summary:
        if target == "?":
            lines.append(f"{source:<22}{'unknown file, not imported'}")
            continue
        lines.append(f"{source:<22}{target or '(skipped on purpose)':<26}{read:>8}{written:>9}{skipped:>9}")
    return "\n".join(lines)


def export(folder):
    """One JSON file per imported table, from the running stock-oracle `db` container, in
    read-only transactions."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    for source, _, _ in IMPORTS:
        sql = f"SELECT coalesce(json_agg(t), '[]') FROM \"{source}\" t"
        # The SQL is passed as $0 so the shell inside the container never re-quotes it.
        out = subprocess.run(
            ["docker", "compose", "-p", "stock-oracle", "exec", "-T",
             "-e", "PGOPTIONS=-c default_transaction_read_only=on", "db",
             "sh", "-c", 'psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -v ON_ERROR_STOP=1 -c "$0"', sql],
            check=True, capture_output=True, text=True)
        (folder / f"{source}.json").write_text(out.stdout, encoding="utf-8")
        print(f"{source}.json")


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m app.stocks.import_oracle",
                                     description="Import stock-oracle's JSON export into the stocks tables.")
    parser.add_argument("dir", help="folder with one <table>.json per stock-oracle table")
    parser.add_argument("--db", help="SQLite file (default: DATABASE_PATH, else data/markmik.db)")
    parser.add_argument("--replace", action="store_true", help="empty the stocks tables it fills first")
    parser.add_argument("--export", action="store_true",
                        help="instead of importing, export the JSON files into dir from stock-oracle's db container")
    args = parser.parse_args(argv)

    if args.export:
        try:
            export(args.dir)
        except subprocess.CalledProcessError as exc:
            sys.exit(f"export failed: {exc.stderr.strip() or exc}")
        return

    path = args.db or os.environ.get("DATABASE_PATH", "data/markmik.db")
    init_db(path)
    conn = connect(path)
    try:
        summary = run(conn, args.dir, replace=args.replace)
    except ImportProblem as exc:
        sys.exit(f"Nothing imported: {exc}")
    finally:
        conn.close()
    print(format_summary(summary))


if __name__ == "__main__":
    main()
