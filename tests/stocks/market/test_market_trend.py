"""Trend maths for the stock page (ported from stock-oracle unchanged but for the import)."""

from datetime import date, timedelta

import pytest

from app.stocks import trend

D0 = date(2026, 1, 1)


def days(n: int) -> list[date]:
    return [D0 + timedelta(days=i) for i in range(n)]


def test_sma_is_none_until_the_window_fills() -> None:
    assert trend.sma([1, 2, 3, 4, 5], 3) == [None, None, 2.0, 3.0, 4.0]
    assert trend.sma([1, 2], 3) == [None, None]
    assert trend.sma([], 3) == []


def test_sma_matches_a_naive_mean_on_a_long_series() -> None:
    values = [float((i * 37) % 11) for i in range(300)]
    fast = trend.sma(values, 50)
    for i in (49, 120, 299):
        assert fast[i] == pytest.approx(sum(values[i - 49 : i + 1]) / 50)


def test_last_cross_finds_the_most_recent_one_in_either_direction() -> None:
    d = days(6)
    fast = [None, 1.0, 3.0, 3.0, 1.0, 2.0]
    slow = [None, 2.0, 2.0, 2.0, 2.0, 2.0]
    # up on day 2 (golden), down on day 4 (death), equal on day 5 doesn't count
    assert trend.last_cross(d, fast, slow) == trend.Cross(d[4], "death")
    assert trend.last_cross(d[:4], fast[:4], slow[:4]) == trend.Cross(d[2], "golden")


def test_no_cross_when_one_side_throughout() -> None:
    d = days(3)
    assert trend.last_cross(d, [3.0, 3.0, 3.0], [1.0, 1.0, 1.0]) is None
    assert trend.last_cross(d, [None] * 3, [None] * 3) is None


@pytest.mark.parametrize(
    ("price", "fast", "slow", "state"),
    [
        (110, 105, 100, "uptrend"),
        (90, 95, 100, "downtrend"),
        (100, 105, 95, "mixed"),
        (110, 95, 100, "mixed"),
        (110, None, 100, None),
        (110, 105, None, None),
    ],
)
def test_trend_state(price: float, fast: float | None, slow: float | None, state: str) -> None:
    assert trend.trend_state(price, fast, slow) == state


def test_relative_uses_common_dates_and_skips_zero_prices() -> None:
    d = days(4)
    stock = [(d[0], 10.0), (d[1], 11.0), (d[2], 0.0), (d[3], 12.0)]
    bench = [(d[0], 5.0), (d[2], 5.0), (d[3], 0.0)]
    assert trend.relative(stock, bench) == [(d[0], 2.0)]


def test_excess_return_from_the_ratio() -> None:
    d = days(3)
    # stock +10%, benchmark flat over 2 bars
    ratio = trend.relative(
        [(d[0], 100.0), (d[1], 105.0), (d[2], 110.0)], [(d[0], 50.0), (d[1], 50.0), (d[2], 50.0)]
    )
    assert trend.excess_return(ratio, 2) == pytest.approx(0.10)
    assert trend.excess_return(ratio, 3) is None


def test_summarise_a_steady_uptrend() -> None:
    n = 260
    closes = [100.0 + i for i in range(n)]
    d = days(n)
    ratio = [(day, 1.0 + i / 1000) for i, day in enumerate(d)]
    s = trend.summarise(d, closes, closes, ratio)
    assert s is not None
    assert s.state == "uptrend"
    assert s.price == closes[-1]
    assert s.sma_fast == pytest.approx(sum(closes[-50:]) / 50)
    assert s.vs_fast is not None and s.vs_fast > 0
    assert s.last_cross is None
    assert s.high_52w == closes[-1] and s.low_52w == closes[-253]
    assert s.range_position == 1.0 and s.drawdown == 0.0
    assert s.excess[20] == pytest.approx(ratio[-1][1] / ratio[-21][1] - 1)


def test_summarise_short_flat_history_without_benchmark() -> None:
    d = days(10)
    s = trend.summarise(d, [5.0] * 10, [5.0] * 10, [])
    assert s is not None
    assert s.state is None and s.sma_fast is None and s.vs_slow is None
    assert s.range_position is None and s.drawdown == 0.0
    assert s.excess == {}


def test_summarise_nothing() -> None:
    assert trend.summarise([], [], [], []) is None


def test_drawdown_and_range_position_below_the_high() -> None:
    d = days(3)
    # The range uses the adjusted closes; the state uses the raw ones.
    s = trend.summarise(d, [1.0, 2.0, 3.0], [100.0, 200.0, 150.0], [])
    assert s is not None
    assert s.price == 3.0
    assert s.drawdown == pytest.approx(-0.25)
    assert s.range_position == pytest.approx(0.5)


def daily(day: str, o: float, h: float, lo: float, c: float) -> dict[str, object]:
    return {"time": day, "open": o, "high": h, "low": lo, "close": c}


def test_resample_weekly_ohlc_volume_and_lines() -> None:
    candles = [
        daily("2026-09-10", 10, 12, 9, 11),  # Thu, ISO week 37
        daily("2026-09-11", 11, 15, 10, 14),  # Fri
        daily("2026-09-14", 14, 14, 8, 9),  # Mon, week 38
    ]
    volume = [
        {"time": "2026-09-10", "value": 100},
        {"time": "2026-09-11", "value": 50},
        {"time": "2026-09-14", "value": 7},
    ]
    lines = {
        "sma_fast": [{"time": "2026-09-10", "value": 1.0}, {"time": "2026-09-11", "value": 2.0}]
    }
    out = trend.resample(candles, volume, lines, "week")
    assert out["candles"] == [daily("2026-09-10", 10, 15, 9, 14), daily("2026-09-14", 14, 14, 8, 9)]
    assert out["volume"] == [
        {"time": "2026-09-10", "value": 150},
        {"time": "2026-09-14", "value": 7},
    ]
    # the week's last value, stamped with the week's first trading day
    assert out["sma_fast"] == [{"time": "2026-09-10", "value": 2.0}]
    assert candles[0]["high"] == 12  # the input isn't modified


def test_resample_monthly_and_iso_weeks_across_new_year() -> None:
    candles = [
        daily("2025-12-30", 1, 2, 1, 2),
        daily("2025-12-31", 2, 3, 2, 3),
        daily("2026-01-02", 3, 4, 3, 4),  # same ISO week (2026-W01) as 30-31 Dec
    ]
    weekly = trend.resample(candles, [], {}, "week")["candles"]
    monthly = trend.resample(candles, [], {}, "month")["candles"]
    assert [c["time"] for c in weekly] == ["2025-12-30"]
    assert weekly[0]["close"] == 4
    assert [c["time"] for c in monthly] == ["2025-12-30", "2026-01-02"]


def test_resample_nothing() -> None:
    assert trend.resample([], [], {"relative": []}, "month") == {
        "candles": [],
        "volume": [],
        "relative": [],
    }
