from datetime import date
from decimal import Decimal

import pytest

from app.util import TALLINN as TZ
from app.stocks.portfolio import TxKind
from app.stocks.csv_import import detect_columns, parse_csv, parse_kind

INSTRUMENTS = {"AAPL": (1, "USD"), "VWCE": (2, "EUR"), "IE00BK5BQT80": (2, "EUR")}


def resolve(symbol: str | None, isin: str | None) -> tuple[int, str] | None:
    return INSTRUMENTS.get((symbol or "").upper()) or INSTRUMENTS.get((isin or "").upper())


def test_english_comma_separated_export() -> None:
    preview = parse_csv(
        "Date,Type,Symbol,Quantity,Price,Commission,Currency,Reference\n"
        "2026-01-05,Deposit,,,,,EUR,d1\n"
        '2026-01-06,Buy,AAPL,10,"1,234.50",1.00,USD,b1\n',
        resolve,
    )
    assert preview.errors == []
    assert preview.delimiter == ","
    deposit, buy = preview.rows
    assert deposit.errors  # a deposit needs an amount
    assert buy.importable
    assert buy.tx is not None
    assert buy.tx.kind is TxKind.BUY
    assert buy.tx.qty == Decimal(10)
    assert buy.tx.price == Decimal("1234.50")
    assert buy.tx.fee == Decimal("1.00")
    assert buy.tx.currency == "USD"
    assert buy.tx.ref == "b1"
    assert buy.tx.instrument == "1"
    assert buy.tx.ts.astimezone(TZ).hour == 12


def test_estonian_semicolon_export_with_decimal_commas() -> None:
    preview = parse_csv(
        "Kuupäev;Tehing;Sümbol;Kogus;Hind;Summa;Valuuta;Tasu;Selgitus\n"
        "05.01.2026;Sissemakse;;;;1 000,00;EUR;;palk\n"
        "06.01.2026;Ost;VWCE;3;120,50;;EUR;0,90;\n"
        "10.03.2026;Dividend;AAPL;;;12,34;USD;;\n",
        resolve,
    )
    assert preview.delimiter == ";"
    assert preview.decimal_mark == ","
    assert preview.columns["date"] == "Kuupäev"
    assert preview.columns["kind"] == "Tehing"
    kinds = [r.kind for r in preview.rows]
    assert kinds == [TxKind.DEPOSIT, TxKind.BUY, TxKind.DIVIDEND]
    assert all(r.importable for r in preview.rows), [r.errors for r in preview.rows]
    deposit, buy, dividend = (r.tx for r in preview.rows)
    assert deposit is not None and deposit.amount == Decimal("1000.00")
    assert buy is not None and buy.price == Decimal("120.50") and buy.fee == Decimal("0.90")
    assert dividend is not None and dividend.amount == Decimal("12.34")
    assert dividend.currency == "USD"
    assert preview.rows[0].note == "palk"


def test_messy_headers_signs_and_embedded_currency() -> None:
    preview = parse_csv(
        "  Trade Date , Action ,ISIN, Shares ,Total Amount, Withholding Tax ,Extra\n"
        "2026-03-01,Dividend payment,AAPL,,$10.00,$1.50,x\n"
        "2026-02-01,SELL,IE00BK5BQT80,-2,EUR 250.00,,x\n"
        "2026-01-01,BUY,IE00BK5BQT80,5,-500.00,,x\n",
        resolve,
    )
    assert preview.unmapped == ["Extra"]
    # Newest-first files are replayed oldest first.
    assert [r.kind for r in preview.rows] == [TxKind.BUY, TxKind.SELL, TxKind.DIVIDEND]
    assert [r.line for r in preview.rows] == [4, 3, 2]
    buy, sell, dividend = (r.tx for r in preview.rows)
    assert buy is not None and buy.qty == 5 and buy.price == Decimal(100)
    assert (
        sell is not None and sell.qty == 2 and sell.price == Decimal(125) and sell.currency == "EUR"
    )
    assert dividend is not None and dividend.currency == "USD"
    assert dividend.tax_withheld == Decimal("1.50")


def test_row_errors_are_reported_per_row() -> None:
    preview = parse_csv(
        "date,type,symbol,qty,price\n"
        "2026-01-01,buy,NOPE,1,10\n"
        "not a date,buy,AAPL,1,10\n"
        "2026-01-01,teleport,AAPL,1,10\n"
        "2026-01-01,buy,AAPL,x,10\n"
        "2026-01-01,buy,AAPL,1,\n",
        resolve,
    )
    errors = [" ".join(r.errors) for r in preview.rows]
    assert "tundmatu väärtpaber „NOPE“" in errors[0]
    assert "pole kuupäev" in errors[1]
    assert "tundmatu tehinguliik „teleport“" in errors[2]
    assert "kogus: „x“ pole arv" in errors[3]
    assert "Hind" in errors[4]
    assert preview.importable == []


def test_missing_required_columns_and_overrides() -> None:
    text = "When,What,Ticker,Units,Cost\n2026-01-01,buy,AAPL,1,10\n"
    preview = parse_csv(text, resolve)
    assert preview.errors and "kuupäev ja liik" in preview.errors[0]
    assert preview.rows == []

    fixed = parse_csv(
        text, resolve, {"date": "When", "kind": "What", "price": "Cost", "bogus": "x"}
    )
    assert fixed.errors == []
    (row,) = fixed.rows
    assert row.importable and row.tx is not None and row.tx.price == 10

    # An override naming no column unmaps the field.
    unmapped = parse_csv(text, resolve, {"date": "When", "kind": "What", "symbol": ""})
    assert "symbol" not in unmapped.columns


def test_identical_rows_are_separate_fills_and_refs_are_stable() -> None:
    text = "date,type,amount\n2026-01-01,deposit,100\n2026-01-01,deposit,100\n"
    first = parse_csv(text, resolve)
    assert [r.duplicate for r in first.rows] == [False, False]
    refs = [r.tx.ref for r in first.rows if r.tx is not None]
    assert len(refs) == 2 and refs[0] != refs[1]
    assert all(ref is not None and ref.startswith("csv:") for ref in refs)
    again = parse_csv(text, resolve)
    assert [r.tx.ref for r in again.rows if r.tx is not None] == refs


def test_repeated_explicit_ref_in_one_file_is_a_duplicate() -> None:
    text = "date,type,amount,ref\n2026-01-01,deposit,100,X1\n2026-01-01,deposit,100,X1\n"
    preview = parse_csv(text, resolve)
    assert [r.duplicate for r in preview.rows] == [False, True]


def test_split_and_times() -> None:
    preview = parse_csv("date;type;symbol;ratio\n10.06.2024 15:30;Stock split;AAPL;4\n", resolve)
    (row,) = preview.rows
    assert row.tx is not None and row.tx.ratio == 4
    assert row.tx.ts.astimezone(TZ).hour == 15
    assert row.tx.trade_date == date(2024, 6, 10)


def test_empty_file() -> None:
    assert parse_csv("﻿\n\n", resolve).errors == ["Fail on tühi."]


def test_domain_validation_message_is_shown() -> None:
    preview = parse_csv("date,type,symbol,amount,tax\n2026-01-01,dividend,AAPL,1,5\n", resolve)
    assert "Kinnipeetud maks on suurem kui brutosumma." in preview.rows[0].errors[0]


@pytest.mark.parametrize(
    ("text", "kind"),
    [
        ("Buy", TxKind.BUY),
        ("Ost", TxKind.BUY),
        ("Müük", TxKind.SELL),
        ("Väljamakse", TxKind.WITHDRAWAL),
        ("Dividends", TxKind.DIVIDEND),
        ("Account fee", TxKind.FEE),
        ("Cost adjustment", None),
        ("", None),
    ],
)
def test_parse_kind(text: str, kind: TxKind | None) -> None:
    assert parse_kind(text) is kind


def test_detect_columns_uses_each_header_once() -> None:
    columns = detect_columns(["Date", "Time", "Amount", "Value"])
    assert columns["date"] == "Date"
    assert columns["amount"] == "Amount"
