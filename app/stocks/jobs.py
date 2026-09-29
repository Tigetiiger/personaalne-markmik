"""Aktsiad's worker jobs (docs/AKTSIAD-PLAN.md, "Nightly and daytime jobs"). Each returns quickly:
the worker runs jobs one after another on one loop, and reminders go every minute. The night
itself runs on a thread of its own (night.start).

    stocks-night    01:30        the night; one the worker missed runs when it starts, before
                                 06:00, and is recorded as skipped after (the worker keeps
                                 job:<name>:next, so a missed slot comes due on start)
    stocks-digest   07:00        the morning digest and the night's problems
    stocks-quotes   every 300 s  daytime quotes and the alerts after them, US market hours only
                                 (judged in New York time, so the weeks when US and EU daylight
                                 saving differ need nothing special)
    stocks-filings  every 3600 s EDGAR 8-K and Form 4 between nights, then the alerts

Nothing runs while no stock is followed or held: a Märkmik without Aktsiad never calls Yahoo.
"""

import logging
from datetime import timedelta

from ..jobs import job
from ..util import TALLINN, now_utc
from . import alerts, common, market, night, sources

log = logging.getLogger("markmik.stocks.jobs")


def _db_path(conn):
    return conn.execute("PRAGMA database_list").fetchone()["file"]


def in_use(conn):
    return bool(alerts.tracked(conn)[0])


def in_night_window(now):
    local = now.astimezone(TALLINN).date()
    return night.at(local, night.START) <= now < night.at(local, night.DEADLINE)


@job("stocks-night", at=(night.START.strftime("%H:%M"),))
def nightly(conn, now=None):
    now = now or now_utc()
    day = now.astimezone(TALLINN).date()
    if not in_use(conn):
        return
    if now < night.at(day, night.START):
        # The worker started after midnight but before tonight's 01:30: what it missed was
        # yesterday's night, long past its 06:00. Tonight's slot comes next.
        night.record_skipped(conn, day - timedelta(days=1), now)
        return
    if now >= night.at(day, night.DEADLINE):
        night.record_skipped(conn, day, now)
        return
    if not night.start(_db_path(conn), day):
        log.info("stocks night %s: already running", day)


@job("stocks-digest", at=(alerts.DIGEST_AT.strftime("%H:%M"),))
def digest(conn, now=None):
    now = now or now_utc()
    local = now.astimezone(TALLINN)
    # A worker that was off at 07:00 and starts after midnight must not send tomorrow's digest
    # before its night: that day's own 07:00 slot comes next.
    if local.time() < alerts.DIGEST_AT:
        return
    alerts.send_digest(conn, local.date())


@job("stocks-quotes", every=300)
def quotes(conn, now=None):
    now = now or now_utc()
    if not market.us_market_open(now) or not in_use(conn):
        return
    result = market.refresh_quotes(conn, now)
    alerts.check(conn, now)
    if result.get("failed"):
        log.warning("stock quotes failed for %s", sorted(result["failed"]))


@job("stocks-filings", every=3600)
def filings(conn, now=None):
    now = now or now_utc()
    # The night reads EDGAR itself; two readers at once would only race each other.
    if in_night_window(now) or common.claimed(conn, night.CLAIM) or common.claimed(conn, "ingest") or not in_use(conn):
        return
    sources.poll_filings(conn, now)
    alerts.check(conn, now)
