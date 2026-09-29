"""Aktsiad: the pages (one template, a tab per page) and Seaded → Aktsiad. The data behind each
tab comes from its package's blueprint (stocks_market, stocks_portfolio, stocks_sources,
stocks_ai, stocks_night; docs/CONTRACTS.md, "Aktsiad").

Owner: lead."""

from flask import Blueprint, abort, jsonify, render_template, request

from ..db import get_db
from ..stocks import config
from ..stocks.config import SYMBOL

bp = Blueprint("stocks", __name__)

# (path under /aktsiad, data-page, tab label)
TABS = (
    ("", "stocks-watchlist", "Jälgin"),
    ("portfell", "stocks-portfolio", "Portfell"),
    ("tehingud", "stocks-tx", "Tehingud"),
    ("avasta", "stocks-explore", "Avasta"),
    ("allikad", "stocks-sources", "Allikad"),
)
TITLES = {page: label for _, page, label in TABS}


def _page(page, title=None, **kw):
    return render_template("stocks.html", page=page, title=title or TITLES[page], tabs=TABS, **kw)


@bp.get("/aktsiad")
def watchlist():
    return _page("stocks-watchlist")


@bp.get("/aktsiad/portfell")
def portfolio():
    return _page("stocks-portfolio")


@bp.get("/aktsiad/tehingud")
def transactions():
    return _page("stocks-tx")


@bp.get("/aktsiad/avasta")
def explore():
    return _page("stocks-explore")


@bp.get("/aktsiad/allikad")
def sources():
    return _page("stocks-sources")


@bp.get("/aktsiad/<symbol>")
def ticker(symbol):
    # Lowercase paths are the tabs above; a symbol is always upper case (VWCE.DE, BRK-B).
    if not SYMBOL.fullmatch(symbol):
        abort(404)
    return _page("stocks-ticker", symbol=symbol, title=symbol)


@bp.get("/api/stocks/settings")
def get_settings():
    return jsonify(config.settings(get_db()))


@bp.put("/api/stocks/settings")
def put_settings():
    try:
        return jsonify(config.save_settings(get_db(), request.get_json(silent=True)))
    except ValueError as exc:
        return jsonify(error=str(exc)), 400
