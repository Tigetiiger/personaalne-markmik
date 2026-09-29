"""The attention z-score: a day's document count against the days before it. Ported from
stock-oracle's sources/attention.py (attention_zscores), for one series at a time."""

import math
from datetime import date, timedelta


def zscore(by_day: dict[date, float], day: date, window: int = 30, min_history: int = 7, *,
           first: date | None = None) -> float | None:
    """z of `day`'s count against the `window` calendar days before it.

    Days without a count inside the series are zero mentions, because that is what an absent day
    means for a count. Days before the series' first count are not part of the baseline (nothing
    was collected yet); `first` is that day when `by_day` holds only part of the series. None while
    the baseline is shorter than `min_history` or flat."""
    if not by_day and first is None:
        return None
    first = first or min(by_day)
    start = max(first, day - timedelta(days=window))
    baseline = [by_day.get(start + timedelta(days=i), 0.0) for i in range((day - start).days)]
    if len(baseline) < min_history:
        return None
    mean = sum(baseline) / len(baseline)
    std = math.sqrt(sum((x - mean) ** 2 for x in baseline) / (len(baseline) - 1))
    if std == 0:
        return None
    return (by_day.get(day, 0.0) - mean) / std
