"""Aktsiad: the night package's API (docs/CONTRACTS.md, "Aktsiad"): the nightly runs, starting
tonight's run now, and Täna's "Turg" card. Owner only (default deny), JSON, Estonian errors.

Owner: WP stocks-night."""

import json
import os
from datetime import datetime, timedelta, timezone
from decimal import Decimal

from flask import Blueprint, current_app, jsonify, request

from ..db import get_db, tx
from ..security import rate_limit
from ..stocks import alerts, common, night
from ..util import local_today

bp = Blueprint("stocks_night", __name__)

RUNS_SHOWN = 10
SIX = Decimal("0.000001")


def _s(value, quant=None):
    return None if value is None else format(value.quantize(quant) if quant else value, "f")


@bp.get("/api/stocks/night")
def status():
    conn = get_db()
    runs = [{"id": r["id"], "run_date": r["run_date"], "status": r["status"], "started_at": r["started_at"],
             "finished_at": r["finished_at"],
             # A list: jsonify sorts object keys, and the steps' order is the night's.
             "steps": [{"name": name, **step} for name, step in json.loads(r["steps_json"] or "{}").items()]}
            for r in conn.execute("SELECT * FROM stock_runs ORDER BY started_at DESC, id DESC LIMIT ?", (RUNS_SHOWN,))]
    return jsonify(running=common.claimed(conn, night.CLAIM), runs=runs)


@bp.post("/api/stocks/night/run")
@rate_limit("stocks-night", 3, 3600)
def run_now():
    if not night.start(current_app.config["DATABASE_PATH"], local_today()):
        return jsonify(error="Öö juba käib."), 409
    return jsonify(running=True), 202


@bp.get("/api/stocks/today")
def today():
    """Täna's "Turg": the day's move of every holding, earnings today or tomorrow of anything
    followed or held, and the verdicts last night changed."""
    conn = get_db()
    day = local_today()
    rows, held = alerts.tracked(conn)
    if not rows:
        return jsonify(followed=False, holdings=[], earnings=[], verdicts=[])
    by_id = {r["id"]: r for r in rows}
    holdings = []
    for row in rows:
        if row["id"] not in held:
            continue
        move = alerts.day_move(conn, row["id"])
        holdings.append({"symbol": row["symbol"], "name": row["name"], "currency": row["currency"],
                         "price": _s(move and move["price"]), "previous_close": _s(move and move["previous_close"]),
                         "change": _s(move and move["change"], SIX), "day": move and move["day"].isoformat()})
    holdings.sort(key=lambda h: (h["change"] is None, -abs(Decimal(h["change"] or 0)), h["symbol"]))
    marks = ",".join("?" * len(by_id))
    earnings = [{"symbol": by_id[r["instrument_id"]]["symbol"], "name": by_id[r["instrument_id"]]["name"], "date": r["date"],
                 "time_of_day": r["t"], "held": r["instrument_id"] in held}
                for r in conn.execute(
                    f"SELECT instrument_id, date, MAX(time_of_day) AS t FROM earnings WHERE instrument_id IN ({marks}) "
                    f"AND date IN (?, ?) GROUP BY instrument_id, date", (*by_id, day.isoformat(), (day + timedelta(days=1)).isoformat()))]
    earnings.sort(key=lambda e: (e["date"], e["symbol"]))
    verdicts = [{"symbol": v["symbol"], "name": v["name"], "verdict": v["verdict"], "conviction": v["conviction"],
                 "previous": v["previous"]} for v in alerts.verdict_changes(conn, day) if v["instrument_id"] in by_id]
    return jsonify(followed=True, holdings=holdings, earnings=earnings, verdicts=verdicts)


# --- Browser test harness (MARKMIK_TEST_PAGES=1 only) ------------------------------------------

def _seed():
    """Browser tests: replace every stock row with {instruments: [{symbol, name, currency, watch?,
    held?: qty, bars?: [{date, close}]}], earnings: [{symbol, date, time_of_day?}], verdicts:
    [{symbol, as_of, verdict, conviction}]}."""
    data = request.get_json()
    conn = get_db()
    common.clear_all(conn)
    with tx(conn):
        ids = {}
        account = None
        for spec in data.get("instruments", []):
            iid = conn.execute("INSERT INTO instruments (symbol, exchange, currency, name, kind) VALUES (?, 'NMS', ?, ?, 'stock')",
                               (spec["symbol"], spec.get("currency", "USD"), spec.get("name", spec["symbol"]))).lastrowid
            ids[spec["symbol"]] = iid
            if spec.get("watch", True):
                conn.execute("INSERT INTO watchlist (instrument_id) VALUES (?)", (iid,))
            for bar in spec.get("bars", []):
                conn.execute("INSERT INTO price_bars (instrument_id, date, close, source) VALUES (?, ?, ?, 'yahoo')",
                             (iid, bar["date"], str(bar["close"])))
            if spec.get("held"):
                if account is None:
                    account = conn.execute("INSERT INTO broker_accounts (name, broker, kind) VALUES ('Test', 'test', 'brokerage')").lastrowid
                ts = (datetime.now(timezone.utc) - timedelta(days=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
                conn.execute("INSERT INTO portfolio_tx (account_id, instrument_id, kind, ts, qty, price, currency) "
                             "VALUES (?, ?, 'buy', ?, ?, '100', ?)", (account, iid, ts, str(spec["held"]), spec.get("currency", "USD")))
        for e in data.get("earnings", []):
            conn.execute("INSERT INTO earnings (instrument_id, date, time_of_day, source) VALUES (?, ?, ?, 'test')",
                         (ids[e["symbol"]], e["date"], e.get("time_of_day")))
        for v in data.get("verdicts", []):
            conn.execute("INSERT INTO verdicts (instrument_id, as_of, verdict, conviction, summary, prompt_version, model) "
                         "VALUES (?, ?, ?, ?, 'Test', 'test', 'test')", (ids[v["symbol"]], v["as_of"], v["verdict"], v["conviction"]))
    return jsonify(ids)


@bp.record
def _test_pages(state):
    if os.environ.get("MARKMIK_TEST_PAGES") == "1":
        state.app.add_url_rule("/_test/stocks-night/seed", "stocks_night.test_seed", _seed, methods=["POST"])
