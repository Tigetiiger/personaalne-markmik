"""Trend maths for the stock page (stock-oracle's PLAN.md Phase 11, ported as-is). Pure functions
over daily closes, oldest first; nothing here touches the database."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

FAST = 50
SLOW = 200
YEAR = 252

TrendState = Literal["uptrend", "downtrend", "mixed"]


@dataclass(frozen=True)
class Cross:
    date: date
    kind: Literal["golden", "death"]


@dataclass(frozen=True)
class TrendSummary:
    state: TrendState | None
    price: float
    sma_fast: float | None
    sma_slow: float | None
    vs_fast: float | None
    vs_slow: float | None
    last_cross: Cross | None
    high_52w: float
    low_52w: float
    range_position: float | None
    drawdown: float
    excess: dict[int, float | None]


def sma(values: Sequence[float], n: int) -> list[float | None]:
    """Simple moving average; None until n values are available."""
    out: list[float | None] = []
    total = 0.0
    for i, value in enumerate(values):
        total += value
        if i >= n:
            total -= values[i - n]
        out.append(total / n if i >= n - 1 else None)
    return out


def last_cross(
    dates: Sequence[date], fast: Sequence[float | None], slow: Sequence[float | None]
) -> Cross | None:
    """The most recent day the fast average moved above (golden) or below (death)
    the slow one. A day where they are equal doesn't count as a side."""
    side = 0
    found: Cross | None = None
    for day, f, s in zip(dates, fast, slow, strict=True):
        if f is None or s is None or f == s:
            continue
        now = 1 if f > s else -1
        if side and now != side:
            found = Cross(day, "golden" if now > 0 else "death")
        side = now
    return found


def trend_state(price: float, fast: float | None, slow: float | None) -> TrendState | None:
    if fast is None or slow is None:
        return None
    if price > fast > slow:
        return "uptrend"
    if price < fast < slow:
        return "downtrend"
    return "mixed"


def _ratio(a: float, b: float | None) -> float | None:
    return a / b - 1 if b else None


def relative(
    stock: Sequence[tuple[date, float]], bench: Sequence[tuple[date, float]]
) -> list[tuple[date, float]]:
    """Stock / benchmark on the dates both have, as a ratio (the page rebases it)."""
    lookup = {day: value for day, value in bench}
    return [(day, value / lookup[day]) for day, value in stock if lookup.get(day) and value > 0]


def excess_return(ratio: Sequence[tuple[date, float]], bars: int) -> float | None:
    """Stock return minus benchmark return over the last `bars` common days, via
    the ratio: (1 + r_s) / (1 + r_b) - 1, which is close to r_s - r_b."""
    if len(ratio) <= bars:
        return None
    return _ratio(ratio[-1][1], ratio[-1 - bars][1])


def summarise(
    dates: Sequence[date],
    closes: Sequence[float],
    adjusted: Sequence[float],
    ratio: Sequence[tuple[date, float]],
    horizons: Sequence[int] = (20, 60, YEAR),
) -> TrendSummary | None:
    """Averages and state from closes (the candles' scale); the 52-week range and
    drawdown from adjusted closes over YEAR + 1 bars, as the key stats compute them."""
    if not closes:
        return None
    fast, slow = sma(closes, FAST), sma(closes, SLOW)
    price = closes[-1]
    year = adjusted[-(YEAR + 1) :]
    high, low, last = max(year), min(year), year[-1]
    return TrendSummary(
        state=trend_state(price, fast[-1], slow[-1]),
        price=price,
        sma_fast=fast[-1],
        sma_slow=slow[-1],
        vs_fast=_ratio(price, fast[-1]),
        vs_slow=_ratio(price, slow[-1]),
        last_cross=last_cross(dates, fast, slow),
        high_52w=high,
        low_52w=low,
        range_position=(last - low) / (high - low) if high > low else None,
        drawdown=last / high - 1 if high else 0.0,
        excess={h: excess_return(ratio, h) for h in horizons} if ratio else {},
    )


Period = Literal["week", "month"]


def _period(day: str, period: Period) -> tuple[int, int]:
    d = date.fromisoformat(day)
    if period == "month":
        return (d.year, d.month)
    iso = d.isocalendar()
    return (iso.year, iso.week)


def resample(
    candles: Sequence[dict[str, Any]],
    volume: Sequence[dict[str, Any]],
    lines: Mapping[str, Sequence[dict[str, Any]]],
    period: Period,
) -> dict[str, list[dict[str, Any]]]:
    """Daily chart payload grouped into weekly or monthly bars.

    A bar is stamped with its first trading day and holds the first open, the
    highest high, the lowest low, the last close and the summed volume. Line
    series (averages, vs-SPY ratio) keep their value on the period's last
    trading day, stamped like the bar, so they line up with it.
    """
    out: list[dict[str, Any]] = []
    stamp: dict[tuple[int, int], str] = {}
    for c in candles:
        key = _period(c["time"], period)
        if key not in stamp:
            stamp[key] = c["time"]
            out.append(dict(c))
            continue
        bar = out[-1]
        bar["high"] = max(bar["high"], c["high"])
        bar["low"] = min(bar["low"], c["low"])
        bar["close"] = c["close"]
    summed: dict[str, dict[str, Any]] = {}
    for v in volume:
        t = stamp.get(_period(v["time"], period))
        if t is None:
            continue
        entry = summed.setdefault(t, {"time": t, "value": 0})
        entry["value"] += v["value"]
    result: dict[str, list[dict[str, Any]]] = {"candles": out, "volume": list(summed.values())}
    for name, points in lines.items():
        last: dict[str, dict[str, Any]] = {}
        for p in points:
            t = stamp.get(_period(p["time"], period))
            if t is not None:
                last[t] = {"time": t, "value": p["value"]}
        result[name] = list(last.values())
    return result
