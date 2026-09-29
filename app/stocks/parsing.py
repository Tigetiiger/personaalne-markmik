"""Lenient parsing of numbers and dates typed by a person or exported by a broker (from stock-oracle).
ParseError messages are Estonian: the form and the CSV preview show them as they are."""

import re
from datetime import date, datetime
from decimal import Decimal, InvalidOperation

_CURRENCY_TOKENS = re.compile(r"\b[A-Z]{3}\b|[€$£]")
_SPACES = re.compile(r"[\s\N{NO-BREAK SPACE}\N{NARROW NO-BREAK SPACE}']")


class ParseError(ValueError):
    pass


def parse_decimal(text: str | None, decimal_mark: str | None = None) -> Decimal | None:
    """ "1 234,56", "1.234,56", "1,234.56", "-12.5 USD", "€3,10" -> Decimal.

    Blank is None. With `decimal_mark` given ("," or ".") the other one is a
    thousands separator. Without it: when both appear, the last one is the
    decimal mark; a lone comma is a decimal comma unless it appears more than
    once ("1,234,567"). So "1,234" is read as 1.234, as an Estonian export
    means it; the CSV importer infers the mark for the whole file instead.
    """
    if text is None:
        return None
    raw = _SPACES.sub("", _CURRENCY_TOKENS.sub("", text.strip()))
    raw = raw.replace("\N{MINUS SIGN}", "-")
    if raw in ("", "-"):
        return None
    negative = raw.startswith("(") and raw.endswith(")")
    raw = raw.strip("()")
    if decimal_mark is not None:
        thousands = "." if decimal_mark == "," else ","
        raw = raw.replace(thousands, "").replace(decimal_mark, ".")
    elif "," in raw and "." in raw:
        decimal_mark = "," if raw.rfind(",") > raw.rfind(".") else "."
        thousands = "." if decimal_mark == "," else ","
        raw = raw.replace(thousands, "").replace(decimal_mark, ".")
    elif raw.count(",") > 1:
        raw = raw.replace(",", "")
    elif "," in raw:
        raw = raw.replace(",", ".")
    elif raw.count(".") > 1:
        raw = raw.replace(".", "")
    try:
        value = Decimal(raw)
    except InvalidOperation:
        raise ParseError(f"„{text}“ pole arv") from None
    if not value.is_finite():
        raise ParseError(f"„{text}“ pole arv")
    return -value if negative else value


_DATE_FORMATS = (
    "%Y-%m-%d",
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%d-%m-%Y",
    "%Y/%m/%d",
    "%d.%m.%y",
)


def parse_date(text: str | None) -> tuple[date, tuple[int, int] | None]:
    """A date, optionally with a time: returns (date, (hour, minute) or None).

    Day-first for ambiguous slashed dates: the broker and the user are in Estonia.
    """
    if text is None or not text.strip():
        raise ParseError("kuupäev puudub")
    raw = text.strip().replace("T", " ")
    day_part, _, time_part = raw.partition(" ")
    parsed = None
    for fmt in _DATE_FORMATS:
        try:
            # Only the calendar date is kept, so the naive datetime never escapes.
            parsed = datetime.strptime(day_part, fmt).date()  # noqa: DTZ007
            break
        except ValueError:
            continue
    if parsed is None:
        raise ParseError(f"„{text}“ pole kuupäev")
    clock = None
    if time_part.strip():
        match = re.match(r"^(\d{1,2}):(\d{2})", time_part.strip())
        if not match or int(match[1]) > 23 or int(match[2]) > 59:
            raise ParseError(f"„{time_part}“ pole kellaaeg")
        clock = (int(match[1]), int(match[2]))
    return parsed, clock
