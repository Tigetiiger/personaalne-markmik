"""Background jobs run by the worker process (app/worker.py). A module registers its jobs at
import time; the worker imports every module listed in JOB_MODULES.

    @job("gmail-sync", every=300)
    def sync_gmail(conn): ...

    @job("ekool-sync", at=("07:00", "13:00", "17:00", "21:00"))
    def sync_ekool(conn): ...

`every` is seconds between runs; `at` is local (Europe/Tallinn) wall-clock times. A job gets its
own sqlite3 connection and must not assume a Flask context.
"""

import random
from dataclasses import dataclass
from datetime import datetime, time, timedelta, timezone

from .util import TALLINN

# Modules that register jobs. A module that does not exist yet is skipped (work in progress).
JOB_MODULES = (
    "app.reminders",
    "app.sources.base",
    "app.extract",
    "app.gitsync",
    "app.gbrain",
    "app.backup",
    "app.collab",
    "app.voistlused",
    "app.stocks.jobs",
    "app.meedia.jobs",
)


@dataclass(frozen=True)
class Job:
    name: str
    fn: object
    every: int | None
    at: tuple
    jitter: int


REGISTRY = {}


def job(name, every=None, at=(), jitter=30):
    if (every is None) == (not at):
        raise ValueError("give exactly one of every= or at=")

    def register(fn):
        REGISTRY[name] = Job(name, fn, every, tuple(at), jitter)
        return fn
    return register


def next_run(j, last, now, rng=random):
    """When the job should next run. last: previous run (aware) or None; now: aware datetime."""
    if j.every is not None:
        if last is None:
            return now
        return last + timedelta(seconds=j.every + rng.uniform(0, j.jitter))
    # The earliest scheduled wall-clock time after the last run (or after now on first start).
    ref = (last or now).astimezone(TALLINN)
    for day in range(0, 3):
        d = ref.date() + timedelta(days=day)
        # fold=0: in the repeated autumn hour, the first occurrence. In UTC, because two
        # datetimes in the same zone compare by wall clock, which runs back in that hour.
        slots = [datetime.combine(d, time(*map(int, hhmm.split(":"))), tzinfo=TALLINN).astimezone(timezone.utc)
                 for hhmm in j.at]
        # The smallest, not the first in clock order: on the spring-forward night a missing 03:30
        # becomes 04:30, after a 04:00 slot.
        later = [c for c in slots if c > ref]
        if later:
            return max(min(later), now) if last is None else min(later)
    raise AssertionError("unreachable: some slot exists within three days")
