"""Helpers shared by the portfolio tests."""

from datetime import date, datetime
from decimal import Decimal

from app.util import TALLINN as TZ


def D(value: str | int) -> Decimal:
    return Decimal(value)


def at(day: str, time: str = "12:00") -> datetime:
    """A Tallinn wall-clock timestamp, e.g. at("2026-01-05")."""
    return datetime.fromisoformat(f"{day} {time}").replace(tzinfo=TZ)


def d(day: str) -> date:
    return date.fromisoformat(day)


class Fx:
    """ECB-style table: EUR per unit of foreign currency, by date. A lookup uses
    the latest rate on or before the date, like weekend ECB lookups."""

    def __init__(self, table: dict[str, dict[str, str]]) -> None:
        # {"USD": {"2026-01-05": "0.90", ...}}
        self.table = {
            ccy: sorted((d(day), Decimal(rate)) for day, rate in rates.items())
            for ccy, rates in table.items()
        }
        self.calls: list[tuple[date, str, str]] = []

    def eur_per(self, ccy: str, on: date) -> Decimal:
        if ccy == "EUR":
            return Decimal(1)
        rates = [rate for day, rate in self.table[ccy] if day <= on]
        if not rates:
            raise LookupError(f"no {ccy} rate on or before {on}")
        return rates[-1]

    def __call__(self, on: date, from_ccy: str, to_ccy: str) -> Decimal:
        self.calls.append((on, from_ccy, to_ccy))
        return self.eur_per(from_ccy, on) / self.eur_per(to_ccy, on)


def no_fx(on: date, from_ccy: str, to_ccy: str) -> Decimal:
    raise AssertionError(f"unexpected FX lookup {on} {from_ccy}->{to_ccy}")
