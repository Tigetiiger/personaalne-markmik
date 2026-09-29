"""The portfolio JSON API: accounts, transactions with the replay check, the CSV import, the
Portfell summary and a stock's position (after stock-oracle's e2e tests for the same pages)."""

import io
from datetime import date

import pytest
from portfolio_support import add_account, add_bars, add_fx, add_instrument, add_tx

from app.util import TALLINN, parse_iso


def form(account, **fields):
    return {"account_id": str(account), "date": "2026-09-01", **fields}


def kinds(conn):
    return [r[0] for r in conn.execute("SELECT kind FROM portfolio_tx ORDER BY ts, id")]


# --- Accounts --------------------------------------------------------------------------------------

def test_accounts(owner, client):
    r = owner.post("/api/stocks/accounts", json={"name": "LHV IK", "kind": "investment_account"})
    assert r.status_code == 201 and r.get_json()["name"] == "LHV IK" and r.get_json()["broker"] == "LHV"
    assert owner.post("/api/stocks/accounts", json={"name": "LHV IK", "kind": "brokerage"}).status_code == 409
    assert owner.post("/api/stocks/accounts", json={"name": " ", "kind": "brokerage"}).status_code == 400
    assert owner.post("/api/stocks/accounts", json={"name": "X", "kind": "pension"}).status_code == 400
    assert [(a["name"], a["kind"], a["tx_count"]) for a in owner.get("/api/stocks/accounts").get_json()] == [
        ("LHV IK", "investment_account", 0)]
    for path in ("/api/stocks/accounts", "/api/stocks/tx", "/api/stocks/portfolio", "/api/stocks/position/ACME"):
        assert client.get(path).status_code == 401
    assert client.post("/api/stocks/tx", json={}).status_code in (401, 403)


# --- Transactions ----------------------------------------------------------------------------------

def test_add_buy_saves_and_watches(owner, conn):
    account = add_account(conn)
    acme = add_instrument(conn, "ACME", watch=False)
    add_tx(conn, account, "deposit", date(2026, 8, 1), amount=5000)
    r = owner.post("/api/stocks/tx", json=form(account, kind="buy", instrument_id=str(acme), qty="10", price="123,45",
                                               fee="1", amount="999", note="first lot", date="2026-09-01 15:30"))
    assert r.status_code == 201, r.get_json()
    saved = r.get_json()
    assert saved["symbol"] == "ACME" and saved["values"]["date"] == "2026-09-01 15:30"
    row = conn.execute("SELECT * FROM portfolio_tx WHERE kind = 'buy'").fetchone()
    # The amount is a hidden field for buys: ignored.
    assert (row["qty"], row["price"], row["fee"], row["amount"]) == ("10", "123.45", "1", None)
    assert row["currency"] == "USD" and row["note"] == "first lot"
    assert parse_iso(row["ts"]).astimezone(TALLINN).hour == 15
    assert conn.execute("SELECT 1 FROM watchlist WHERE instrument_id = ?", (acme,)).fetchone()

    listing = owner.get("/api/stocks/tx").get_json()
    assert [t["values"]["kind"] for t in listing["rows"]] == ["buy", "deposit"]
    assert [i["symbol"] for i in listing["instruments"]] == ["ACME"]
    assert listing["uses"]["dividend"] == ["instrument_id", "amount", "tax_withheld"]


def test_validation_errors_per_field(owner, conn):
    account = add_account(conn)
    r = owner.post("/api/stocks/tx", json=form(account, kind="buy", qty="ten", price="", date="31.02.2026"))
    assert r.status_code == 400
    assert r.get_json()["errors"] == {
        "date": "„31.02.2026“ pole kuupäev.",
        "instrument_id": "Vali väärtpaber.",
        "qty": "Kirjuta arv, nt 12,50.",
        "price": "Kohustuslik.",
    }
    assert owner.post("/api/stocks/tx", json=form(account, kind="gift")).get_json()["errors"] == {"kind": "Vali tehingu liik."}
    no_account = owner.post("/api/stocks/tx", json={"kind": "deposit", "date": "2026-01-01", "amount": "5"})
    assert no_account.get_json()["errors"]["account_id"] == "Vali konto."
    assert owner.post("/api/stocks/tx", data="x").status_code == 400
    assert conn.execute("SELECT COUNT(*) FROM portfolio_tx").fetchone()[0] == 0


def test_domain_rules_and_oversell_are_refused(owner, conn):
    account = add_account(conn)
    acme = add_instrument(conn, "ACME")
    negative = owner.post("/api/stocks/tx", json=form(account, kind="deposit", amount="-5"))
    assert negative.status_code == 400 and negative.get_json()["error"] == "Summa puudub või pole positiivne."
    oversell = owner.post("/api/stocks/tx", json=form(account, kind="sell", instrument_id=acme, qty="1", price="10"))
    assert oversell.status_code == 400
    assert oversell.get_json()["error"] == "Nii ei klapiks konto ajalugu. Müüd 1 ACME, aga 01.09.2026 seisuga on sul ainult 0."
    assert conn.execute("SELECT COUNT(*) FROM portfolio_tx").fetchone()[0] == 0


def test_every_kind_can_be_entered(owner, conn):
    account = add_account(conn)
    iid = str(add_instrument(conn, "ACME"))
    entries = [
        {"kind": "deposit", "amount": "1 000,00", "currency": "eur"},
        {"kind": "buy", "instrument_id": iid, "qty": "4", "price": "100"},
        {"kind": "split", "instrument_id": iid, "ratio": "2"},
        {"kind": "sell", "instrument_id": iid, "qty": "3", "price": "60", "fee": "0.5"},
        {"kind": "dividend", "instrument_id": iid, "amount": "2", "tax_withheld": "0.30"},
        {"kind": "interest", "amount": "0.12"},
        {"kind": "fee", "amount": "3"},
        {"kind": "withdrawal", "amount": "50"},
    ]
    for n, entry in enumerate(entries, start=1):
        r = owner.post("/api/stocks/tx", json=form(account, date=f"2026-09-{n:02d}", **entry))
        assert r.status_code == 201, (entry, r.get_json())
    assert kinds(conn) == [e["kind"] for e in entries]
    assert len(owner.get("/api/stocks/tx").get_json()["rows"]) == len(entries)


def test_edit_and_delete_replay_the_account(owner, conn):
    account = add_account(conn)
    acme = add_instrument(conn, "ACME")
    deposit = add_tx(conn, account, "deposit", date(2026, 9, 1), amount=100)
    buy = add_tx(conn, account, "buy", date(2026, 9, 2), instrument=acme, currency="USD", qty=2, price=10)
    sell = add_tx(conn, account, "sell", date(2026, 9, 3), instrument=acme, currency="USD", qty=2, price=12)
    listed = {t["id"]: t for t in owner.get("/api/stocks/tx").get_json()["rows"]}
    assert listed[buy]["values"]["qty"] == "2" and listed[buy]["values"]["date"] == "2026-09-02 13:00"

    # Shrinking the buy below what was sold is refused and the row is unchanged.
    refused = owner.put(f"/api/stocks/tx/{buy}", json=form(account, kind="buy", instrument_id=acme, qty="1", price="10",
                                                           date="2026-09-02"))
    assert refused.status_code == 400 and "ainult 1" in refused.get_json()["error"]
    assert conn.execute("SELECT qty FROM portfolio_tx WHERE id = ?", (buy,)).fetchone()[0] == "2"

    saved = owner.put(f"/api/stocks/tx/{buy}", json=form(account, kind="buy", instrument_id=acme, qty="3", price="9.5",
                                                         date="2026-09-02"))
    assert saved.status_code == 200 and saved.get_json()["id"] == buy
    assert tuple(conn.execute("SELECT qty, price FROM portfolio_tx WHERE id = ?", (buy,)).fetchone()) == ("3", "9.5")

    # Moving the buy to another account would leave the sell selling shares never bought.
    other = add_account(conn, "Teine", "brokerage")
    moved = owner.put(f"/api/stocks/tx/{buy}", json=form(other, kind="buy", instrument_id=acme, qty="3", price="9.5",
                                                         date="2026-09-02"))
    assert moved.status_code == 400 and "ACME" in moved.get_json()["error"]

    blocked = owner.delete(f"/api/stocks/tx/{buy}")
    assert blocked.status_code == 409 and blocked.get_json()["error"].startswith("Ei kustutatud.")
    for tx_id in (sell, buy, deposit):
        assert owner.delete(f"/api/stocks/tx/{tx_id}").status_code == 204
    assert kinds(conn) == []
    assert owner.delete(f"/api/stocks/tx/{buy}").status_code == 404
    assert owner.put(f"/api/stocks/tx/{buy}", json=form(account, kind="deposit", amount="1")).status_code == 404


def test_one_currency_per_instrument_across_accounts(owner, conn):
    a, b = add_account(conn, "A"), add_account(conn, "B", "brokerage")
    acme = add_instrument(conn, "ACME")
    add_tx(conn, a, "buy", date(2026, 9, 1), instrument=acme, currency="USD", qty=1, price=10)
    r = owner.post("/api/stocks/tx", json=form(b, kind="buy", instrument_id=acme, qty="1", price="9", currency="EUR", date="2026-09-02"))
    assert r.status_code == 400
    assert r.get_json()["error"] == "Nii ei klapiks konto ajalugu. ACME tehingud on seni olnud valuutas USD, see on EUR."


def test_the_list_holds_the_last_500(owner, conn):
    account = add_account(conn)
    for n in range(501):
        add_tx(conn, account, "deposit", date(2020, 1, 1), amount=n + 1)
    rows = owner.get("/api/stocks/tx").get_json()["rows"]
    assert len(rows) == 500 and rows[0]["values"]["amount"] == "501"


def test_an_index_cannot_be_traded(owner, conn):
    account = add_account(conn)
    spy = add_instrument(conn, "^GSPC", kind="index")
    r = owner.post("/api/stocks/tx", json=form(account, kind="buy", instrument_id=spy, qty="1", price="1"))
    assert r.get_json()["errors"] == {"instrument_id": "Vali väärtpaber."}
    assert owner.get("/api/stocks/tx").get_json()["instruments"] == []


# --- CSV import ------------------------------------------------------------------------------------

# Semicolons, decimal commas, Estonian headers, EUR and USD rows, an unused column.
ESTONIAN = (
    "Kuupäev;Tehing;Sümbol;Kogus;Hind;Summa;Valuuta;Tasu;Viide;Konto\n"
    "02.01.2026;Sissemakse;;;;2 000,00;EUR;;r1;EE12\n"
    "05.01.2026;Ost;ACME;10;110,00;;USD;1,00;r2;EE12\n"
    "02.02.2026;Ost;VWCE;5;100,00;;EUR;;r3;EE12\n"
    "01.06.2026;Dividend;ACME;;;10,00;USD;;r4;EE12\n"
    "03.06.2026;Ost;UNKNOWN;1;1,00;;USD;;r5;EE12\n"
)


@pytest.fixture
def seeded(conn):
    add_instrument(conn, "ACME", watch=False)
    add_instrument(conn, "VWCE", kind="etf", currency="EUR", exchange="XETRA")
    return add_account(conn, "LHV IK")


def upload(owner, account, content, encoding="utf-8", **fields):
    data = {"account_id": str(account), "file": (io.BytesIO(content.encode(encoding)), "export.csv"), **fields}
    return owner.post("/api/stocks/import/preview", data=data, content_type="multipart/form-data")


def test_preview_then_confirm_then_reimport(owner, conn, seeded):
    r = upload(owner, seeded, ESTONIAN)
    assert r.status_code == 200, r.get_json()
    p = r.get_json()
    assert p["columns"]["date"] == "Kuupäev" and p["columns"]["qty"] == "Kogus" and p["columns"]["ref"] == "Viide"
    assert "isin" not in p["columns"] and p["unmapped"] == ["Konto"] and p["decimal_mark"] == ","
    assert (p["ready"], p["total"]) == (4, 5)
    status = {row["line"]: row["status"] for row in p["rows"]}
    assert status[6].startswith("tundmatu väärtpaber „UNKNOWN“") and status[2] == "valmis"
    usd_buy = next(row for row in p["rows"] if row["line"] == 3)
    assert (usd_buy["currency"], usd_buy["price"], usd_buy["fee"], usd_buy["date"]) == ("USD", "110.00", "1.00", "2026-01-05")
    assert "Sissemakse" in p["csv_text"]

    # Confirm posts the same text back, as the browser does.
    body = {"csv_text": p["csv_text"], "account_id": seeded}
    done = owner.post("/api/stocks/import/confirm", json=body)
    assert done.status_code == 200 and done.get_json() == {"imported": 4, "skipped": 1, "account": "LHV IK"}
    rows = conn.execute("SELECT * FROM portfolio_tx ORDER BY ts").fetchall()
    assert [r["kind"] for r in rows] == ["deposit", "buy", "buy", "dividend"]
    assert rows[0]["amount"] == "2000.00" and rows[1]["price"] == "110.00" and rows[1]["fee"] == "1.00"
    assert rows[1]["currency"] == "USD" and rows[1]["external_ref"] == "r2"
    assert conn.execute("SELECT 1 FROM watchlist WHERE instrument_id = ?", (rows[1]["instrument_id"],)).fetchone()

    again = upload(owner, seeded, ESTONIAN).get_json()
    assert again["ready"] == 0 and next(r for r in again["rows"] if r["line"] == 2)["status"] == "juba imporditud"
    nothing = owner.post("/api/stocks/import/confirm", json=body)
    assert nothing.get_json() == {"imported": 0, "skipped": 5, "account": "LHV IK"}
    assert len(kinds(conn)) == 4


def test_identical_fills_both_import_once(owner, conn, seeded):
    content = "date,type,symbol,qty,price,currency\n2026-01-05,buy,ACME,1,100,USD\n2026-01-05,buy,ACME,1,100,USD\n"
    body = {"csv_text": content, "account_id": seeded}
    assert owner.post("/api/stocks/import/confirm", json=body).get_json()["imported"] == 2
    assert owner.post("/api/stocks/import/confirm", json=body).get_json()["imported"] == 0
    assert len(kinds(conn)) == 2


def test_mapping_override_re_previews(owner, seeded):
    content = "When,What,Ticker,Units,Cost\n2026-01-05,deposit,,,500\n"
    first = upload(owner, seeded, content).get_json()
    assert first["errors"] == ["Veergu ei leitud: kuupäev ja liik. Vali see allpool vastavuste all."]
    overridden = owner.post("/api/stocks/import/preview", json={
        "csv_text": content, "account_id": seeded, "mapping": {"date": "When", "kind": "What", "amount": "Cost"}}).get_json()
    assert (overridden["ready"], overridden["total"]) == (1, 1)
    via_form = upload(owner, seeded, content, map_date="When", map_kind="What", map_amount="Cost").get_json()
    assert via_form["ready"] == 1


def test_confirm_refuses_an_oversell_and_saves_nothing(owner, conn, seeded):
    r = owner.post("/api/stocks/import/confirm", json={"csv_text": "date,type,symbol,qty,price\n2026-01-05,sell,ACME,1,10\n",
                                                       "account_id": seeded})
    assert r.status_code == 409 and "ainult 0" in r.get_json()["error"]
    assert kinds(conn) == []


def test_confirm_needs_an_account_and_preview_a_file(owner, seeded):
    r = owner.post("/api/stocks/import/confirm", json={"csv_text": "date,type,amount\n2026-01-01,deposit,5\n", "account_id": ""})
    assert r.status_code == 400 and r.get_json()["error"] == "Vali konto, kuhu importida."
    empty = owner.post("/api/stocks/import/preview", json={"csv_text": " "})
    assert empty.status_code == 400 and empty.get_json()["error"] == "Vali CSV-fail."


def test_baltic_encoded_upload_and_the_size_cap(owner, seeded):
    p = upload(owner, seeded, "Kuupäev;Tehing;Summa\n02.01.2026;Sissemakse;10,00\n", encoding="cp1257").get_json()
    assert (p["ready"], p["columns"]["date"]) == (1, "Kuupäev")
    data = {"file": (io.BytesIO(b"a" * 2_000_001), "x.csv")}
    too_big = owner.post("/api/stocks/import/preview", data=data, content_type="multipart/form-data")
    assert too_big.status_code == 413 and too_big.get_json()["error"] == "Fail on suurem kui 2 MB."
    assert owner.post("/api/stocks/import/confirm", json={"csv_text": "a" * 2_000_001, "account_id": seeded}).status_code == 413
    just_fits = {"file": (io.BytesIO(b"date,type,amount\n" + b"\n" * (2_000_000 - 17)), "x.csv")}
    assert owner.post("/api/stocks/import/preview", data=just_fits, content_type="multipart/form-data").status_code == 200


# --- Summary and position --------------------------------------------------------------------------

@pytest.fixture
def example(conn):
    add_fx(conn, date(2023, 1, 2), "USD", "1.10")
    acme = add_instrument(conn, "ACME")
    add_bars(conn, acme, [120, 132], end=date(2026, 9, 20))
    account = add_account(conn, "LHV IK")
    add_tx(conn, account, "deposit", date(2026, 1, 2), amount=2000)
    add_tx(conn, account, "buy", date(2026, 1, 5), instrument=acme, currency="USD", qty=10, price=110)
    return acme


def test_portfolio_summary_json(owner, example):
    p = owner.get("/api/stocks/portfolio").get_json()
    assert p["has_transactions"] and p["problems"] == []
    # 1320 USD at 1.10 = 1200 EUR; cash 1000 EUR.
    assert p["summary"]["value"] == "2200.00" and p["summary"]["unrealised"]["eur"] == "200.00"
    assert p["positions"][0]["qty"] == "10" and p["positions"][0]["price"] == "132"
    assert p["history"][-1]["date"] == p["as_of"] and p["investment"]["balance"] == "2000.00"
    assert p["allocation"]["instrument"] == [{"label": "ACME", "value": "1200.00", "weight": "1.000000"}]


def test_position_json(owner, conn, example):
    p = owner.get("/api/stocks/position/ACME").get_json()
    assert (p["symbol"], p["qty"], p["avg_cost"], p["value_eur"], p["unrealised_pct"]) == (
        "ACME", "10", "110.000000", "1200.00", "0.200000")
    assert p["accounts"] == [{"name": "LHV IK", "qty": "10"}]
    add_instrument(conn, "NVDA")
    assert owner.get("/api/stocks/position/NVDA").get_json() is None
    assert owner.get("/api/stocks/position/NOPE").get_json() is None
    assert owner.get("/api/stocks/position/a b").status_code == 404


def test_the_test_seed_route_is_absent(owner):
    assert owner.post("/_test/stocks-portfolio/seed", json={}).status_code == 404
