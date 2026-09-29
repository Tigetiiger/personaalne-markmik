"""Form 4 ownership XML (ported from stock-oracle's test_form4.py)."""

from datetime import date
from decimal import Decimal

import pytest

from app.stocks.sources.form4 import Form4Error, parse_form4
from news_helpers import fixture_bytes


def test_sale_by_ceo_through_trust() -> None:
    form = parse_form4(fixture_bytes("edgar/form4_sale.xml"))
    assert form.document_type == "4"
    assert not form.amendment
    assert form.issuer_cik == "0001045810"
    assert form.issuer_ticker == "NVDA"
    assert form.owners == ("HUANG JEN HSUN", "Jen-Hsun & Lori Huang Living Trust")

    first, second = form.transactions
    assert first.insider_name == "HUANG JEN HSUN"
    assert first.insider_cik == "0001197647"
    assert first.insider_role == "Director; Officer (President and CEO)"
    assert first.kind == "sell"
    assert first.code == "S"
    assert first.acquired_disposed == "D"
    assert first.shares == Decimal("75000")
    # The price has a footnote next to it; the value is still used.
    assert first.price == Decimal("181.2345")
    assert first.shares_after == Decimal("70123456")
    assert first.direct is False
    assert first.derivative is False
    assert first.trade_date == date(2026, 9, 8)

    # Thousands separator, a date with an offset suffix, price only in a footnote.
    assert second.shares == Decimal("1500")
    assert second.trade_date == date(2026, 9, 8)
    assert second.price is None


def test_holdings_rows_are_not_transactions() -> None:
    form = parse_form4(fixture_bytes("edgar/form4_sale.xml"))
    assert len(form.transactions) == 2


def test_amendment_with_derivative_table_and_namespace() -> None:
    form = parse_form4(fixture_bytes("edgar/form4a_derivative.xml").decode())
    assert form.amendment
    assert all(t.amendment for t in form.transactions)
    codes = [(t.code, t.derivative) for t in form.transactions]
    # The phantom-unit row has only a total value, no share count: skipped.
    assert codes == [("M", False), ("F", False), ("P", False), ("M", True), ("A", True)]

    exercise, tax, buy, rsu, option = form.transactions
    assert exercise.kind == "other"
    assert tax.shares == Decimal("4012.5")
    assert tax.kind == "other"
    assert buy.kind == "buy"
    assert buy.price == Decimal("178.00")
    assert buy.insider_role == "Officer (EVP and CFO)"
    assert rsu.security_title == "Restricted Stock Units"
    assert rsu.underlying_shares == Decimal("10000")
    assert rsu.exercise_price is None
    assert rsu.shares_after == Decimal("30000")
    # No transactionDate: falls back to the period of report.
    assert option.trade_date == date(2026, 9, 5)
    assert option.exercise_price == Decimal("95.50")
    assert option.price is None
    assert option.shares_after is None


def test_role_other_text_and_missing_relationship() -> None:
    xml = """<ownershipDocument><documentType>4</documentType>
      <periodOfReport>2026-09-01</periodOfReport>
      <reportingOwner><reportingOwnerId><rptOwnerName>Fund LP</rptOwnerName></reportingOwnerId>
      </reportingOwner>
      <nonDerivativeTable><nonDerivativeTransaction>
        <securityTitle><value>Common</value></securityTitle>
        <transactionCoding><transactionCode>p</transactionCode></transactionCoding>
        <transactionAmounts><transactionShares><value>10</value></transactionShares>
          <transactionPricePerShare><value>n/a</value></transactionPricePerShare>
        </transactionAmounts>
      </nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""
    (t,) = parse_form4(xml).transactions
    assert t.insider_role is None
    assert t.insider_cik is None
    assert t.code == "P"
    assert t.price is None
    assert t.direct is True
    assert t.trade_date == date(2026, 9, 1)


def test_ten_percent_owner_role() -> None:
    xml = """<ownershipDocument><documentType>4</documentType>
      <reportingOwner><reportingOwnerId><rptOwnerName>Big Holder</rptOwnerName></reportingOwnerId>
      <reportingOwnerRelationship><isTenPercentOwner>1</isTenPercentOwner><isOther>1</isOther>
      </reportingOwnerRelationship></reportingOwner></ownershipDocument>"""
    form = parse_form4(xml)
    assert form.transactions == ()
    assert form.period_of_report is None


def test_row_without_any_date_is_skipped() -> None:
    xml = """<ownershipDocument><documentType>4</documentType>
      <reportingOwner><reportingOwnerId><rptOwnerName>X</rptOwnerName></reportingOwnerId>
      </reportingOwner>
      <nonDerivativeTable><nonDerivativeTransaction>
        <transactionDate><value>garbage</value></transactionDate>
        <transactionCoding><transactionCode>S</transactionCode></transactionCoding>
        <transactionAmounts><transactionShares><value>10</value></transactionShares>
        </transactionAmounts>
      </nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""
    assert parse_form4(xml).transactions == ()


@pytest.mark.parametrize(
    ("xml", "message"),
    [
        ("<ownershipDocument><documentType>4", "well-formed"),
        ("<edgarSubmission/>", "ownershipDocument"),
        ("<ownershipDocument><documentType>3</documentType></ownershipDocument>", "not a Form 4"),
        ("<ownershipDocument><documentType>4</documentType></ownershipDocument>", "reportingOwner"),
    ],
)
def test_rejects_non_form4(xml: str, message: str) -> None:
    with pytest.raises(Form4Error, match=message):
        parse_form4(xml)


def test_refuses_entity_declarations():
    xml = ('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaaaaaaaa">]>'
           "<ownershipDocument><documentType>4</documentType></ownershipDocument>")
    with pytest.raises(Form4Error, match="DTD"):
        parse_form4(xml)
