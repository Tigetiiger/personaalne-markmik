"""Aktsiad foundation: the pages, Seaded → Aktsiad, the Turuandmed card and the shared helpers."""

from decimal import Decimal

import pytest

from app import vault
from app.stocks import common, config

HTML = {"Accept": "text/html"}


@pytest.mark.parametrize("path, page", [("/aktsiad", "stocks-watchlist"), ("/aktsiad/portfell", "stocks-portfolio"),
                                        ("/aktsiad/tehingud", "stocks-tx"), ("/aktsiad/avasta", "stocks-explore"),
                                        ("/aktsiad/allikad", "stocks-sources"), ("/aktsiad/VWCE.DE", "stocks-ticker")])
def test_pages(owner, path, page):
    r = owner.get(path, headers=HTML)
    assert r.status_code == 200
    assert f'data-page="{page}"' in r.text


def test_pages_need_the_owner(client):
    r = client.get("/aktsiad", headers=HTML)
    assert r.status_code == 302 and "/login" in r.headers["Location"]
    assert client.get("/api/stocks/settings").status_code == 401


def test_ticker_symbol_is_upper_case(owner):
    assert owner.get("/aktsiad/nvda", headers=HTML).status_code == 404
    assert 'data-symbol="BRK-B"' in owner.get("/aktsiad/BRK-B", headers=HTML).text


def test_settings_defaults_and_save(owner):
    cfg = owner.get("/api/stocks/settings").get_json()
    assert cfg["deep_days"] == ["mon", "thu"] and cfg["ntfy_topic"] == "stock-oracle"
    r = owner.put("/api/stocks/settings", json={"deep_days": ["thu", "sun"], "tax_rates": {"2027": "0,24"},
                                                "ir_feeds": {"nvda": ["https://nvidianews.nvidia.com/rss"]}})
    assert r.status_code == 200, r.get_json()
    cfg = r.get_json()
    assert cfg["deep_days"] == ["thu", "sun"] and cfg["tax_rates"] == {"2027": "0.24"}
    assert cfg["ir_feeds"] == {"NVDA": ["https://nvidianews.nvidia.com/rss"]}
    assert owner.get("/api/stocks/settings").get_json()["research_model"] == "sonnet"  # untouched keys stay


@pytest.mark.parametrize("body", [{"deep_days": ["esmaspäev"]}, {"research_model": "haiku"}, {"alert_move_pct": 0},
                                  {"ntfy_topic": "a b"}, {"tax_rates": {"2026": "1.5"}}, {"opening_balance": "-1"},
                                  {"ir_feeds": {"NVDA": ["http://x.example/rss"]}}, {"night_cap": 2.5}, {"tundmatu": 1},
                                  {"alert_move_pct": True}])
def test_settings_refuse_bad_values(owner, body):
    r = owner.put("/api/stocks/settings", json=body)
    assert r.status_code == 400 and r.get_json()["error"]


def test_tax_rates_as_decimals(conn):
    assert config.tax_rates(conn)[2025] == Decimal("0.22")


def test_turuandmed_card_masks_and_keeps(owner, conn):
    r = owner.post("/api/connections/stocks", json={"finnhub_key": "finnhub-secret-0001", "sec_user_agent": "Markmik a@b.ee"})
    assert r.status_code == 200, r.get_json()
    card = r.get_json()
    assert "finnhub-secret-0001" not in r.text and card["masked"]["finnhub_key"] == "••••0001"
    # An empty field keeps what is stored.
    assert owner.post("/api/connections/stocks", json={"alphavantage_key": "av-secret-00002"}).status_code == 200
    assert vault.get(conn, "finnhub_key") == "finnhub-secret-0001" and vault.get(conn, "alphavantage_key") == "av-secret-00002"
    assert owner.post("/api/connections/stocks", json={"sec_user_agent": "no contact"}).status_code == 400
    assert owner.post("/api/connections/stocks", json={}).status_code == 400
    assert owner.delete("/api/connections/stocks").status_code == 204
    assert all(vault.get(conn, n) is None for n in config.SECRETS)


def test_wizard_has_no_stocks_step(owner):
    assert "stocks" not in [s["key"] for s in owner.get("/api/onboarding").get_json()["steps"]]


def test_dtext_never_takes_a_float():
    assert common.dtext(Decimal("1.10")) == "1.10" and common.dtext(3) == "3" and common.dtext(None) is None
    with pytest.raises(TypeError):
        common.dtext(0.1)
    with pytest.raises(ValueError):
        common.dtext("NaN")
    assert common.dec("2.5") == Decimal("2.5") and common.dec("") is None


def test_claim_is_exclusive_until_released(conn):
    assert common.claim(conn, "night")
    assert not common.claim(conn, "night") and common.claimed(conn, "night")
    assert common.claim(conn, "deep-dive")  # another name is another claim
    common.release(conn, "night")
    assert common.claim(conn, "night")


def test_by_symbol_prefers_the_followed_listing(conn):
    conn.execute("INSERT INTO instruments (symbol, exchange, currency, name, kind) VALUES ('ABC', 'X', 'EUR', 'A', 'stock')")
    b = conn.execute("INSERT INTO instruments (symbol, exchange, currency, name, kind) VALUES ('ABC', 'Y', 'USD', 'B', 'stock')").lastrowid
    conn.execute("INSERT INTO watchlist (instrument_id) VALUES (?)", (b,))
    assert common.by_symbol(conn, "abc")["exchange"] == "Y"
    assert [r["exchange"] for r in common.followed(conn)] == ["Y"]
