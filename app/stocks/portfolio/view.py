"""Everything the Portfell tab and a stock's "Sinu positsioon" show, computed from the portfolio
maths over the database (ported from stock-oracle's web/portfolio_view.py).

Positions, allocation and returns are across all accounts together; the investment-account panel
uses only investment accounts (the tax rules pool all of them). A missing price falls back to the
last traded price and is flagged; a missing FX rate is reported instead of failing the page.
Prices come from `quotes` and `price_bars` as the market package stores them; bars are
split-adjusted, so a close before a split is multiplied back by the split's ratio (the ledger
keeps the pre-split quantity until the split transaction).
"""

import bisect
from contextlib import suppress
from datetime import date, timedelta
from decimal import Decimal

from .. import config
from ..common import dec
from .allocation import by_currency, by_instrument, by_kind, holdings_eur
from .dividends import dividends, yield_on_cost
from .investment_account import UnknownTaxYear, contribution_balance, deferred_tax, year_summary
from .ledger import build_book
from .messages import estonian
from .model import PortfolioError, TxKind
from .pnl import lots_cost_eur, market_value_eur, pnl_summary, unrealised_pnl
from .repository import DbFx, load_all, load_instruments, load_transactions
from .returns import account_xirr, external_flows, twr, valuations

HISTORY_STEP_DAYS = 7
RECENT_DIVIDENDS = 20
KIND_LABELS = {"stock": "Aktsiad", "etf": "ETF-id"}
CENT = Decimal("0.01")
FRACTION = Decimal("0.000001")


def money(value):
    return None if value is None else value.quantize(CENT)


def fraction(value):
    return None if value is None else value.quantize(FRACTION)


def _marks(ids):
    return ", ".join("?" * len(ids))


def closes(conn, ids, since=None):
    """{instrument_id: ([dates], [closes])}, oldest first, one bar per day (the first source by
    name when two sources have the same day)."""
    ids = list(ids)
    out = {i: ([], []) for i in ids}
    if not ids:
        return out
    rows = conn.execute(
        f"SELECT instrument_id, date, close FROM (SELECT instrument_id, date, close, ROW_NUMBER() OVER "
        f"(PARTITION BY instrument_id, date ORDER BY source) AS n FROM price_bars WHERE instrument_id IN ({_marks(ids)}) "
        f"AND date >= ?) WHERE n = 1 ORDER BY instrument_id, date", (*ids, (since or date.min).isoformat()))
    for row in rows:
        days, values = out[row["instrument_id"]]
        days.append(date.fromisoformat(row["date"]))
        values.append(dec(row["close"]))
    return out


def splits(conn, ids):
    """{instrument_id: [(ex-date, ratio)]} from `corporate_actions`, oldest first."""
    ids = list(ids)
    out = {i: [] for i in ids}
    if not ids:
        return out
    rows = conn.execute(
        f"SELECT instrument_id, date, MIN(ratio) AS ratio FROM corporate_actions WHERE kind = 'split' "
        f"AND instrument_id IN ({_marks(ids)}) GROUP BY instrument_id, date ORDER BY instrument_id, date", ids)
    for row in rows:
        out[row["instrument_id"]].append((date.fromisoformat(row["date"]), dec(row["ratio"])))
    return out


def latest(conn, ids):
    """{instrument_id: {price, previous_close, as_of, source}}: the daytime quote, else the last bar."""
    ids = list(ids)
    out = {}
    if not ids:
        return out
    for row in conn.execute(f"SELECT * FROM quotes WHERE instrument_id IN ({_marks(ids)})", ids):
        out[row["instrument_id"]] = {"price": dec(row["price"]), "previous_close": dec(row["previous_close"]),
                                     "as_of": row["as_of"][:10], "source": row["source"]}
    missing = [i for i in ids if i not in out]
    if missing:
        rows = conn.execute(
            f"SELECT instrument_id, date, close, source, n FROM (SELECT instrument_id, date, close, source, "
            f"ROW_NUMBER() OVER (PARTITION BY instrument_id ORDER BY date DESC, source) AS n FROM price_bars "
            f"WHERE instrument_id IN ({_marks(missing)})) WHERE n <= 2 ORDER BY instrument_id, n", missing)
        for row in rows:
            if row["n"] == 1:
                out[row["instrument_id"]] = {"price": dec(row["close"]), "previous_close": None,
                                             "as_of": row["date"], "source": row["source"]}
            elif row["date"] != out[row["instrument_id"]]["as_of"]:
                out[row["instrument_id"]]["previous_close"] = dec(row["close"])
    return out


def _trade_prices(transactions):
    out = {}
    for tx in sorted(transactions, key=lambda t: t.ts):
        if tx.kind in (TxKind.BUY, TxKind.SELL) and tx.instrument and tx.price is not None:
            out.setdefault(tx.instrument, []).append((tx.trade_date, tx.price))
    return out


class PriceBook:
    """Close on or before a day per instrument (undoing later splits), falling back to the last
    trade."""

    def __init__(self, closes, splits, trades):
        self.series = {str(i): s for i, s in closes.items()}
        self.splits = {str(i): s for i, s in splits.items()}
        self.trades = trades

    def market(self, day, key):
        days, values = self.series.get(key, ([], []))
        i = bisect.bisect_right(days, day)
        if not i:
            return None
        price = values[i - 1]
        for ex_date, ratio in self.splits.get(key, []):
            if ex_date > days[i - 1]:
                price *= ratio
        return price

    def __call__(self, day, key):
        price = self.market(day, key)
        if price is not None:
            return price
        trades = [p for d, p in self.trades.get(key, []) if d <= day]
        if not trades:
            raise PortfolioError(f"no price for instrument {key} on {day}")
        return trades[-1]


def _history_dates(first, as_of, flows):
    # The day before the first flow starts at zero, so TWR sees the first deposit as a flow.
    days = {first - timedelta(days=1), as_of, *(d for d, _ in flows)}
    day = as_of
    while day > first:
        days.add(day)
        day -= timedelta(days=HISTORY_STEP_DAYS)
    return sorted(days)


def _pnl(p):
    return None if p is None else {"local": money(p.local), "eur": money(p.eur), "price": money(p.price_effect_eur),
                                   "fx": money(p.fx_effect_eur)}


def _unrecorded_splits(transactions, split_table, rows):
    """Splits the market data knows of, after an instrument was first bought, that no split
    transaction records: the quantity after them is wrong until the owner adds one."""
    recorded = {(tx.instrument, tx.trade_date) for tx in transactions if tx.kind is TxKind.SPLIT}
    first_buy = {}
    for tx in transactions:
        if tx.kind is TxKind.BUY:
            first_buy.setdefault(tx.instrument, tx.trade_date)
    out = []
    for iid, items in split_table.items():
        key = str(iid)
        for ex_date, ratio in items:
            if key in first_buy and ex_date > first_buy[key] and (key, ex_date) not in recorded:
                out.append(f"{rows[key]['symbol']}: {ex_date:%d.%m.%Y} oli split (suhe {ratio.normalize():f}), aga "
                           f"tehingutes seda pole. Lisa split Tehingute lehel, muidu on kogus vale.")
    return out


def build(conn, as_of):
    """The Portfell tab's numbers as of `as_of` (a Tallinn date). Decimals, dates, None."""
    accounts = conn.execute("SELECT * FROM broker_accounts ORDER BY name").fetchall()
    transactions = load_transactions(conn, [a["id"] for a in accounts])
    view = {"as_of": as_of, "has_transactions": bool(transactions), "problems": [], "summary": None,
            "positions": [], "allocation": {}, "history": [], "dividends": {"years": [], "recent": []},
            "investment": None}
    if not transactions:
        return view
    fx = DbFx(conn)
    keys = sorted({tx.instrument for tx in transactions if tx.instrument}, key=int)
    rows = {str(r["id"]): r for r in conn.execute(
        f"SELECT * FROM instruments WHERE id IN ({_marks(keys)})", [int(k) for k in keys])} if keys else {}
    symbols = {k: r["symbol"] for k, r in rows.items()}

    try:
        book = build_book(transactions, as_of)
    except PortfolioError as exc:
        view["problems"].append(f"Tehingud ei klapi: {estonian(exc, symbols)}")
        return view

    first_day = min(tx.trade_date for tx in transactions)
    ids = [int(k) for k in keys]
    split_table = splits(conn, ids)
    prices_at = PriceBook(closes(conn, ids, since=first_day - timedelta(days=10)), split_table,
                          _trade_prices(transactions))
    lasts = latest(conn, [int(k) for k in book.positions])
    view["problems"] += _unrecorded_splits(transactions, split_table, rows)

    prices, price_as_of, market_priced = {}, {}, {}
    for key in book.positions:
        last = lasts.get(int(key))
        if last is not None:
            prices[key], price_as_of[key], market_priced[key] = last["price"], last["as_of"], True
        else:
            prices[key], price_as_of[key], market_priced[key] = prices_at(as_of, key), None, False
            view["problems"].append(f"{symbols[key]}: turuhinda pole salvestatud, väärtus on viimase tehingu hinnaga.")

    try:
        values = holdings_eur(book.positions, prices, as_of, fx)
        s = pnl_summary(transactions, as_of, prices, fx)
        view["summary"] = {
            "value": money(s.value), "cash": money(s.cash), "holdings_value": money(s.holdings_value),
            "net_invested": money(s.net_invested), "total": money(s.total), "realised": _pnl(s.realised),
            "unrealised": _pnl(s.unrealised), "income": money(s.income), "fees": money(s.fees),
            "twr": None, "xirr": None,
        }
    except PortfolioError as exc:
        view["problems"].append(f"Väärtused eurodes puuduvad: {estonian(exc, symbols)}")
        values = {}

    total = sum(values.values(), Decimal(0))
    for key, position in sorted(book.positions.items(), key=lambda kv: -values.get(kv[0], 0)):
        pnl = None
        with suppress(PortfolioError):
            pnl = unrealised_pnl(position, prices[key], as_of, fx)
        row = rows[key]
        view["positions"].append({
            "instrument_id": int(key), "symbol": row["symbol"], "name": row["name"], "kind": row["kind"],
            "currency": position.currency, "qty": position.qty.normalize(), "avg_cost": position.avg_cost.quantize(FRACTION),
            "cost": money(position.cost), "price": prices[key], "price_as_of": price_as_of[key],
            "market_price": market_priced[key], "value": money(position.qty * prices[key]),
            "value_eur": money(values.get(key)), "weight": fraction(values[key] / total) if key in values and total else None,
            "pnl": _pnl(pnl), "pnl_pct": fraction(pnl.local / position.cost) if pnl and position.cost else None,
            "yield_on_cost": fraction(yield_on_cost(transactions, key, as_of)),
        })

    if values:
        try:
            instruments = load_instruments(conn, values.keys())
        except PortfolioError as exc:
            view["problems"].append(estonian(exc, symbols))
        else:
            def slices(pairs):
                return [{"label": label, "value": money(s.value_eur), "weight": fraction(s.weight)}
                        for label, s in sorted(pairs, key=lambda kv: (-kv[1].weight, kv[0]))]
            view["allocation"] = {
                "instrument": slices((symbols[k], s) for k, s in by_instrument(values).items()),
                "currency": slices(by_currency(values, instruments).items()),
                "kind": slices((KIND_LABELS.get(k, k), s) for k, s in by_kind(values, instruments).items()),
            }

    try:
        flows = external_flows(transactions, as_of, fx)
        series = valuations(transactions, _history_dates(first_day, as_of, flows), prices_at, fx)
        invested = Decimal(0)
        flow_on = dict(flows)
        for day, value in series:
            invested += flow_on.get(day, Decimal(0))
            view["history"].append({"date": day, "value": money(value), "invested": money(invested)})
        if flows and view["summary"]:
            view["summary"]["twr"] = fraction(twr(series, flows))
            view["summary"]["xirr"] = fraction(account_xirr(transactions, as_of, series[-1][1], fx))
    except PortfolioError as exc:
        view["problems"].append(f"Tootlust ei saa arvutada: {estonian(exc, symbols)}")

    try:
        paid = dividends(transactions, as_of, fx)
        years = {}
        for d in paid:
            totals = years.setdefault(d.paid.year, [Decimal(0)] * 3)
            totals[0] += d.gross_eur
            totals[1] += d.withheld_eur
            totals[2] += d.net_eur
        view["dividends"] = {
            "years": [{"year": y, "gross": money(g), "withheld": money(w), "net": money(n)}
                      for y, (g, w, n) in sorted(years.items(), reverse=True)],
            "recent": [{"date": d.paid, "symbol": symbols[d.instrument], "currency": d.currency, "gross": money(d.gross),
                        "withheld": money(d.withheld), "withholding_rate": fraction(d.withholding_rate),
                        "gross_eur": money(d.gross_eur), "net_eur": money(d.net_eur)}
                       for d in list(reversed(paid))[:RECENT_DIVIDENDS]],
        }
    except PortfolioError as exc:
        view["problems"].append(f"Dividendid eurodes puuduvad: {estonian(exc, symbols)}")

    ia_accounts = [a for a in accounts if a["kind"] == "investment_account"]
    if ia_accounts:
        view["investment"] = _investment_panel(conn, ia_accounts, as_of, prices, fx, symbols)
    return view


def _investment_panel(conn, accounts, as_of, prices, fx, symbols):
    """Contribution balance, deferred tax and the per-year table 6.5 summary. The rates and the
    opening balance are Seaded → Aktsiad's (stock-oracle read settings it never defined)."""
    rates = config.tax_rates(conn)
    opening = Decimal(config.settings(conn)["opening_balance"])
    txs = load_transactions(conn, [a["id"] for a in accounts])
    panel = {"accounts": [a["name"] for a in accounts], "opening_balance": money(opening), "balance": None,
             "deferred": None, "years": [], "missing_years": [], "problems": []}
    if not txs:
        return panel
    try:
        panel["balance"] = money(contribution_balance(txs, as_of, fx, opening))
        held = build_book(txs, as_of).positions
        d = deferred_tax(txs, as_of, {key: prices[key] for key in held}, fx, rates, opening)
        panel["deferred"] = {"account_value": money(d.account_value), "contribution_balance": money(d.contribution_balance),
                             "taxable_gain": money(d.taxable_gain), "rate": d.rate, "tax": money(d.tax)}
    except UnknownTaxYear:
        panel["missing_years"].append(as_of.year)
    except PortfolioError as exc:
        panel["problems"].append(estonian(exc, symbols))
    first_year = min(tx.trade_date.year for tx in txs)
    for year in range(as_of.year, first_year - 1, -1):
        try:
            y = year_summary(txs, year, fx, rates, opening)
        except UnknownTaxYear:
            panel["missing_years"].append(year)
            continue
        except PortfolioError:
            continue
        panel["years"].append({
            "year": y.year, "opening_balance": money(y.opening_balance), "contributions": money(y.contributions),
            "withdrawals": money(y.withdrawals), "closing_balance": money(y.closing_balance),
            "taxable_amount": money(y.taxable_amount), "rate": y.rate, "tax_due": money(y.tax_due),
            "dividends_gross": money(y.dividends_gross), "dividends_withheld": money(y.dividends_withheld),
            "dividend_top_up": money(y.dividend_top_up),
        })
    panel["missing_years"] = sorted(set(panel["missing_years"]))
    return panel


# --- For the other stocks packages (docs/CONTRACTS.md, "Aktsiad") ----------------------------------

def holdings(conn, as_of=None):
    """{instrument_id: quantity} across all accounts, splits applied, quantity > 0."""
    book = build_book(load_all(conn), as_of or date.max)
    return {int(key): position.qty for key, position in book.positions.items()}


def position(conn, instrument_id, today):
    """{qty, avg_cost, currency, cost_eur, value_eur, unrealised_pct, accounts: [{name, qty}]} and
    also cost, price, price_as_of, market_price, value, gain_eur, or None when not held. The EUR
    figures are None without an FX rate."""
    key = str(instrument_id)
    accounts = conn.execute("SELECT id, name FROM broker_accounts ORDER BY name").fetchall()
    per_account = {a["id"]: load_transactions(conn, [a["id"]]) for a in accounts}
    transactions = [tx for txs in per_account.values() for tx in txs]
    held = build_book(transactions, today).positions.get(key)
    if held is None:
        return None
    last = latest(conn, [instrument_id]).get(instrument_id)
    if last is not None:
        price, as_of, market_price = last["price"], last["as_of"], True
    else:
        trades = _trade_prices(transactions).get(key, [])
        price, as_of, market_price = trades[-1][1], trades[-1][0].isoformat(), False
    fx = DbFx(conn)
    cost_eur = value_eur = None
    with suppress(PortfolioError):
        cost_eur = lots_cost_eur(held.lots, held.currency, fx)
        value_eur = market_value_eur(held, price, today, fx)
    gain = value_eur - cost_eur if value_eur is not None else None
    return {
        "qty": held.qty.normalize(), "avg_cost": held.avg_cost.quantize(FRACTION), "currency": held.currency,
        "cost": money(held.cost), "price": price, "price_as_of": as_of, "market_price": market_price,
        "value": money(held.qty * price), "cost_eur": money(cost_eur), "value_eur": money(value_eur),
        "gain_eur": money(gain), "unrealised_pct": fraction(gain / cost_eur) if gain is not None and cost_eur else None,
        "accounts": [{"name": a["name"], "qty": p.qty.normalize()} for a in accounts
                     if (p := build_book(per_account[a["id"]], today).positions.get(key)) is not None],
    }
