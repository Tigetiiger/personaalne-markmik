from datetime import date
from decimal import Decimal

import pytest

from app.stocks.parsing import ParseError, parse_date, parse_decimal


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("12.50", "12.50"),
        ("12,50", "12.50"),
        ("1 234,56", "1234.56"),
        ("1\N{NO-BREAK SPACE}234,56", "1234.56"),
        ("1.234,56", "1234.56"),
        ("1,234.56", "1234.56"),
        ("1,234,567", "1234567"),
        ("1.234.567", "1234567"),
        ("-12.5 USD", "-12.5"),
        ("\N{MINUS SIGN} 3,10 €", "-3.10"),
        ("(45.00)", "-45.00"),
        ("$1,000.00", "1000.00"),
        ("USD 7", "7"),
        ("1,234", "1.234"),
    ],
)
def test_parse_decimal(text: str, expected: str) -> None:
    assert parse_decimal(text) == Decimal(expected)


@pytest.mark.parametrize(
    ("text", "mark", "expected"),
    [("1,234", ".", "1234"), ("1.234", ",", "1234"), ("1 234,5", ",", "1234.5")],
)
def test_parse_decimal_with_a_known_decimal_mark(text: str, mark: str, expected: str) -> None:
    assert parse_decimal(text, mark) == Decimal(expected)


@pytest.mark.parametrize("text", ["", "  ", None, "-"])
def test_blank_is_none(text: str | None) -> None:
    assert parse_decimal(text) is None


@pytest.mark.parametrize("text", ["abc", "1.2.3,4,5", "NaN", "inf"])
def test_garbage_is_rejected(text: str) -> None:
    with pytest.raises(ParseError):
        parse_decimal(text)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("2026-09-21", (date(2026, 9, 21), None)),
        ("21.09.2026", (date(2026, 9, 21), None)),
        ("21/09/2026", (date(2026, 9, 21), None)),
        ("03/04/2026", (date(2026, 4, 3), None)),
        ("21.09.26", (date(2026, 9, 21), None)),
        ("2026-09-21 14:05", (date(2026, 9, 21), (14, 5))),
        ("2026-09-21T09:30:00", (date(2026, 9, 21), (9, 30))),
    ],
)
def test_parse_date(text: str, expected: tuple[date, tuple[int, int] | None]) -> None:
    assert parse_date(text) == expected


@pytest.mark.parametrize(
    "text", ["", "yesterday", "2026-13-01", "2026-09-21 25:00", "2026-09-21 x"]
)
def test_bad_dates_are_rejected(text: str) -> None:
    with pytest.raises(ParseError):
        parse_date(text)
