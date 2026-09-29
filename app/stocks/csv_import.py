"""Broker CSV import, lenient about the file's shape.

Ported from stock-oracle. LHV's exact export format is not known yet (docs/AKTSIAD-PLAN.md), so the
importer works in two layers:

1. **Column mapping.** Each header is normalised (lower case, accents and
   punctuation dropped) and looked up in `COLUMN_ALIASES`, which maps our
   canonical fields to the header spellings seen in English and Estonian
   exports. The preview shows the mapping and lets the user override it
   per field. When the real LHV sample arrives, add its headers and its
   transaction-type wording (`KIND_KEYWORDS`) here; nothing else should need
   to change.
2. **Row parsing** into domain `Transaction`s, with every problem reported per
   row rather than failing the file.

Nothing here touches the database: instrument lookup is injected. Messages are Estonian: the
preview shows them as they are.
"""

import csv
import hashlib
import io
import re
import unicodedata
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, time
from decimal import Decimal

from ..util import TALLINN as TZ
from .parsing import ParseError, parse_date, parse_decimal
from .portfolio import InvalidTransaction, Transaction, TxKind
from .portfolio.messages import estonian

FIELDS = (
    "date",
    "kind",
    "symbol",
    "isin",
    "qty",
    "price",
    "amount",
    "currency",
    "fee",
    "tax",
    "ratio",
    "ref",
    "note",
)
REQUIRED = ("date", "kind")
LABELS = {
    "date": "kuupäev",
    "kind": "liik",
    "symbol": "sümbol",
    "isin": "ISIN",
    "qty": "kogus",
    "price": "hind",
    "amount": "summa",
    "currency": "valuuta",
    "fee": "tasu",
    "tax": "maks",
    "ratio": "suhe",
    "ref": "viide",
    "note": "märkus",
}

# Normalised header spellings per field. Order within a tuple doesn't matter;
# the first header in the file that matches wins.
COLUMN_ALIASES: Mapping[str, tuple[str, ...]] = {
    "date": (
        "date",
        "tradedate",
        "transactiondate",
        "bookingdate",
        "valuedate",
        "time",
        "datetime",
        "kuupaev",
        "tehingukuupaev",
        "tehingupaev",
        "vaartuspaev",
    ),
    "kind": (
        "type",
        "kind",
        "transactiontype",
        "action",
        "operation",
        "side",
        "tehing",
        "tehinguliik",
        "liik",
        "tuup",
        "selgitusetuup",
    ),
    "symbol": ("symbol", "ticker", "instrument", "security", "sumbol", "vaartpaber", "luhinimi"),
    "isin": ("isin", "isincode", "isinkood"),
    "qty": ("qty", "quantity", "shares", "units", "amountofshares", "kogus", "arv"),
    "price": ("price", "unitprice", "priceper", "pricepershare", "hind", "uhikuhind"),
    "amount": (
        "amount",
        "total",
        "totalamount",
        "value",
        "netamount",
        "grossamount",
        "cash",
        "summa",
        "kokku",
        "netosumma",
    ),
    "currency": ("currency", "ccy", "valuuta"),
    "fee": ("fee", "fees", "commission", "tasu", "teenustasu", "vahendustasu"),
    "tax": (
        "tax",
        "taxwithheld",
        "withholdingtax",
        "withholding",
        "maks",
        "tulumaks",
        "kinnipeetudmaks",
    ),
    "ratio": ("ratio", "splitratio", "suhe"),
    "ref": (
        "ref",
        "reference",
        "id",
        "transactionid",
        "orderid",
        "viide",
        "tehinguid",
        "arhiveerimistunnus",
    ),
    "note": (
        "note",
        "notes",
        "description",
        "comment",
        "details",
        "selgitus",
        "kirjeldus",
        "markus",
    ),
}

# Checked in order against each word of the type text (accents dropped); a
# word matches a keyword it starts with, so "dividends" is a dividend but
# "cost" is not "ost". The first keyword that matches any word wins.
KIND_KEYWORDS: tuple[tuple[str, TxKind], ...] = (
    ("dividend", TxKind.DIVIDEND),
    ("split", TxKind.SPLIT),
    ("withdraw", TxKind.WITHDRAWAL),
    ("valjamakse", TxKind.WITHDRAWAL),
    ("deposit", TxKind.DEPOSIT),
    ("sissemakse", TxKind.DEPOSIT),
    ("interest", TxKind.INTEREST),
    ("intress", TxKind.INTEREST),
    ("sell", TxKind.SELL),
    ("sold", TxKind.SELL),
    ("muuk", TxKind.SELL),
    ("buy", TxKind.BUY),
    ("bought", TxKind.BUY),
    ("purchase", TxKind.BUY),
    ("ost", TxKind.BUY),
    ("fee", TxKind.FEE),
    ("commission", TxKind.FEE),
    ("tasu", TxKind.FEE),
    ("teenustasu", TxKind.FEE),
    ("vahendustasu", TxKind.FEE),
)

_TRADES = {TxKind.BUY, TxKind.SELL}
_WITH_INSTRUMENT = {TxKind.BUY, TxKind.SELL, TxKind.DIVIDEND, TxKind.SPLIT}
_CURRENCY_IN_CELL = re.compile(r"\b([A-Z]{3})\b")
_SYMBOLS = {"€": "EUR", "$": "USD", "£": "GBP"}
_COMMA_DECIMAL = re.compile(r"^[-(]?[\d .\N{NO-BREAK SPACE}]*\d,\d+\)?$")
_DOT_DECIMAL = re.compile(r"^[-(]?[\d ,\N{NO-BREAK SPACE}]*\d\.\d+\)?$")

# (symbol, isin) -> (instrument id, instrument currency), or None when unknown.
Resolver = Callable[[str | None, str | None], tuple[int, str] | None]


def _plain(text: str) -> str:
    # õ/ä/ö/ü/š/ž all decompose into a base letter plus a combining mark.
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch)).lower()


def normalise(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", _plain(header))


def detect_columns(headers: list[str]) -> dict[str, str]:
    """field -> header, for every field whose alias appears among the headers."""
    found: dict[str, str] = {}
    by_norm = {normalise(h): h for h in reversed(headers)}
    for name, aliases in COLUMN_ALIASES.items():
        for alias in aliases:
            if alias in by_norm and by_norm[alias] not in found.values():
                found[name] = by_norm[alias]
                break
    return found


def parse_kind(text: str) -> TxKind | None:
    words = re.findall(r"[a-z]+", _plain(text))
    for keyword, kind in KIND_KEYWORDS:
        if any(word.startswith(keyword) for word in words):
            return kind
    return None


def sniff_delimiter(text: str) -> str:
    sample = "\n".join(text.splitlines()[:20])
    try:
        return csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        return ";" if sample.count(";") > sample.count(",") else ","


def infer_decimal_mark(values: list[str]) -> str | None:
    comma = sum(1 for v in values if _COMMA_DECIMAL.match(v.strip()))
    dot = sum(1 for v in values if _DOT_DECIMAL.match(v.strip()))
    if comma == dot == 0:
        return None
    return "," if comma > dot else "."


@dataclass
class ImportRow:
    line: int
    raw: dict[str, str]
    kind: TxKind | None = None
    symbol: str | None = None
    instrument_id: int | None = None
    tx: Transaction | None = None
    note: str | None = None
    errors: list[str] = field(default_factory=list)
    duplicate: bool = False

    @property
    def importable(self) -> bool:
        return self.tx is not None and not self.errors and not self.duplicate


@dataclass
class Preview:
    headers: list[str]
    columns: dict[str, str]
    delimiter: str
    decimal_mark: str | None
    rows: list[ImportRow]
    errors: list[str] = field(default_factory=list)

    @property
    def importable(self) -> list[ImportRow]:
        return [r for r in self.rows if r.importable]

    @property
    def unmapped(self) -> list[str]:
        used = set(self.columns.values())
        return [h for h in self.headers if h not in used]


def _currency_hint(*cells: str) -> str | None:
    for cell in cells:
        for sign, code in _SYMBOLS.items():
            if sign in cell:
                return code
        match = _CURRENCY_IN_CELL.search(cell)
        if match:
            return match[1]
    return None


def _row_ref(values: dict[str, str], occurrence: int) -> str:
    # Identical rows in one file are separate fills (two buys at the same price
    # and minute), so the n-th copy gets its own ref. Re-importing the same file
    # yields the same refs, which is what makes re-imports skip.
    digest = hashlib.sha256("\x1f".join(values.values()).encode()).hexdigest()
    ref = f"csv:{digest[:20]}"
    return ref if occurrence == 1 else f"{ref}:{occurrence}"


def parse_csv(
    text: str,
    resolve: Resolver,
    overrides: Mapping[str, str] | None = None,
) -> Preview:
    text = text.lstrip("﻿")
    delimiter = sniff_delimiter(text)
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    all_rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not all_rows:
        return Preview([], {}, delimiter, None, [], ["Fail on tühi."])
    headers = [h.strip() for h in all_rows[0]]
    columns = detect_columns(headers)
    for name, header in (overrides or {}).items():
        if name not in FIELDS:
            continue
        if header in headers:
            columns[name] = header
        else:
            columns.pop(name, None)
    preview = Preview(headers, columns, delimiter, None, [])
    missing = [name for name in REQUIRED if name not in columns]
    if missing:
        preview.errors.append(
            "Veergu ei leitud: " + " ja ".join(LABELS[name] for name in missing) + ". Vali see allpool vastavuste all."
        )
        return preview

    records = [
        dict(
            zip(
                headers,
                [cell.strip() for cell in row] + [""] * (len(headers) - len(row)),
                strict=False,
            )
        )
        for row in all_rows[1:]
    ]
    numeric = [
        record[columns[name]]
        for record in records
        for name in ("qty", "price", "amount", "fee", "tax", "ratio")
        if name in columns
    ]
    preview.decimal_mark = infer_decimal_mark(numeric)

    occurrences: Counter[tuple[str, ...]] = Counter()
    for line, record in enumerate(records, start=2):
        occurrences[tuple(record.values())] += 1
        preview.rows.append(
            _parse_row(
                line,
                record,
                columns,
                preview.decimal_mark,
                resolve,
                occurrences[tuple(record.values())],
            )
        )

    # Exports are often newest first; replay needs oldest first so a same-day
    # buy stays ahead of its sell. Only whole-file order is used, never a re-sort.
    dated = [r.tx.ts for r in preview.rows if r.tx is not None]
    if len(dated) > 1 and dated[0] > dated[-1]:
        preview.rows.reverse()

    seen: set[str] = set()
    for row in preview.rows:
        if row.tx is not None and row.tx.ref is not None:
            if row.tx.ref in seen:
                row.duplicate = True
            seen.add(row.tx.ref)
    return preview


def _parse_row(
    line: int,
    record: dict[str, str],
    columns: Mapping[str, str],
    decimal_mark: str | None,
    resolve: Resolver,
    occurrence: int = 1,
) -> ImportRow:
    def cell(name: str) -> str:
        header = columns.get(name)
        return record.get(header, "") if header else ""

    row = ImportRow(line, record, note=cell("note") or None)
    errors = row.errors

    def number(name: str) -> Decimal | None:
        try:
            value = parse_decimal(cell(name), decimal_mark)
        except ParseError:
            errors.append(f"{LABELS[name]}: „{cell(name)}“ pole arv")
            return None
        return abs(value) if value is not None else None

    row.kind = parse_kind(cell("kind"))
    if row.kind is None:
        errors.append(f"tundmatu tehinguliik „{cell('kind')}“")

    try:
        day, clock = parse_date(cell("date"))
    except ParseError as exc:
        errors.append(str(exc))
        day, clock = None, None

    qty, price, amount = number("qty"), number("price"), number("amount")
    fee, tax, ratio = number("fee"), number("tax"), number("ratio")

    instrument_currency = None
    if row.kind in _WITH_INSTRUMENT or (
        row.kind is TxKind.FEE and (cell("symbol") or cell("isin"))
    ):
        row.symbol = cell("symbol") or cell("isin") or None
        found = resolve(cell("symbol") or None, cell("isin") or None)
        if found is None:
            errors.append(
                f"tundmatu väärtpaber „{row.symbol or '(tühi)'}“: lisa see enne Jälgin-lehele"
            )
        else:
            row.instrument_id, instrument_currency = found

    currency = (
        cell("currency").upper()
        or _currency_hint(cell("amount"), cell("price"))
        or instrument_currency
        or "EUR"
    )

    if errors or row.kind is None or day is None:
        return row

    hour, minute = clock or (12, 0)
    ts = datetime.combine(day, time(hour, minute), tzinfo=TZ)
    instrument = str(row.instrument_id) if row.instrument_id is not None else None
    ref = cell("ref") or _row_ref(record, occurrence)
    kind = row.kind
    try:
        if kind in _TRADES:
            if price is None and amount is not None and qty:
                price = amount / qty
            row.tx = Transaction(
                kind, ts, currency, instrument, qty=qty, price=price, fee=fee or Decimal(0), ref=ref
            )
        elif kind in (TxKind.DIVIDEND, TxKind.INTEREST):
            row.tx = Transaction(
                kind,
                ts,
                currency,
                instrument,
                amount=amount,
                tax_withheld=tax or Decimal(0),
                ref=ref,
            )
        elif kind is TxKind.SPLIT:
            row.tx = Transaction(kind, ts, currency, instrument, ratio=ratio, ref=ref)
        else:
            row.tx = Transaction(kind, ts, currency, instrument, amount=amount, ref=ref)
    except InvalidTransaction as exc:
        errors.append(estonian(exc))
    return row
