"""Summer time ends in Europe/Tallinn on Sunday 25.10.2026: at 04:00 EEST (01:00Z) the clocks go
back to 03:00 EET, so local 03:00-03:59 happens twice. These drive the real scheduler
(app.worker.run_due) with a fake clock the way the worker's main loop does, and check the ICS feed
the phone reads around the change."""

from datetime import date, datetime, timedelta, timezone

import pytest
from icalendar import Calendar

from app import events, ics, jobs, reminders, vault, worker
from app.util import TALLINN, iso

UTC = timezone.utc


def utc(text):
    return datetime.fromisoformat(text).replace(tzinfo=UTC)


@pytest.fixture
def registry(monkeypatch):
    """Only the jobs a test registers run."""
    monkeypatch.setattr(jobs, "REGISTRY", {})
    return jobs.REGISTRY


@pytest.fixture
def clock():
    return {"now": None}


def drive(conn, clock, start, end):
    """The worker's main loop with a fake clock: run what is due, then sleep until the next due
    job, at least 1 s and at most 30 s."""
    now = start
    while now < end:
        clock["now"] = now
        soonest = worker.run_due(conn, now)
        now += timedelta(seconds=max(1.0, min(30.0, (soonest - now).total_seconds())))


def recorder(registry, clock, name, at):
    ran = []
    jobs.job(name, at=at)(lambda conn: ran.append(clock["now"]))
    return ran


def test_a_0700_job_runs_at_0700_local_on_both_sides_of_the_change(conn, registry, clock):
    ran = recorder(registry, clock, "qa-0700", ("07:00",))
    drive(conn, clock, utc("2026-10-23T12:00:00"), utc("2026-10-27T00:00:00"))
    assert [iso(t) for t in ran] == ["2026-10-24T04:00:00Z", "2026-10-25T05:00:00Z", "2026-10-26T05:00:00Z"]
    assert [t.astimezone(TALLINN).strftime("%d.%m %H:%M") for t in ran] == ["24.10 07:00", "25.10 07:00", "26.10 07:00"]


def test_a_0330_job_runs_once_a_night_through_the_repeated_hour(conn, registry, clock):
    # backup runs at 03:30, which on 25.10 names two instants: it runs at the first.
    ran = recorder(registry, clock, "qa-0330", ("03:30",))
    drive(conn, clock, utc("2026-10-23T12:00:00"), utc("2026-10-27T00:00:00"))
    assert [iso(t) for t in ran] == ["2026-10-24T00:30:00Z", "2026-10-25T00:30:00Z", "2026-10-26T01:30:00Z"]


@pytest.mark.parametrize("history", [True, False], ids=["after-a-run", "first-start"])
def test_a_worker_starting_in_the_repeated_hour_does_not_rerun_a_0330_job(conn, registry, clock, history):
    # The worker was down over the first 03:30 (00:30Z) and starts again at 03:10 EET (01:10Z),
    # when the wall clock is back before 03:30.
    ran = recorder(registry, clock, "qa-0330", ("03:30",))
    if history:
        worker._set_setting(conn, "job:qa-0330:last", "2026-10-24T00:30:00Z")
        worker._set_setting(conn, "job:qa-0330:next", "2026-10-25T00:30:00Z")
    drive(conn, clock, utc("2026-10-25T01:10:00"), utc("2026-10-26T12:00:00"))
    expected = ["2026-10-25T01:10:00Z"] if history else []  # the missed run, late, once
    assert [iso(t) for t in ran] == expected + ["2026-10-26T01:30:00Z"]


def test_reminders_across_the_change_fire_once_at_the_right_instant(conn, registry, clock, monkeypatch):
    registry["reminders"] = jobs.Job("reminders", reminders.run, 60, (), 30)
    monkeypatch.setattr(reminders, "now_utc", lambda: clock["now"])
    pushed = []
    monkeypatch.setattr(reminders, "publish", lambda cfg, title, message: pushed.append((f"{title} {message}", clock["now"])))
    vault.set_json(conn, "ntfy", {"url": "http://ntfy.invalid", "topic": "qa", "token": None})

    def add(title, **fields):
        events.create(conn, {"title": title, "status": "kinnitatud", **fields})
        return title

    fire_at = {  # title -> when its reminder is due (UTC)
        # tests: 18:00 local the evening before; the 24th is still summer time, the 25th is not
        add("KT pühapäeval", kind="kontrolltöö", start_at="2026-10-25T07:00:00Z"): "2026-10-24T15:00:00Z",
        add("KT esmaspäeval", kind="kontrolltöö", all_day=1, start_date="2026-10-26"): "2026-10-25T16:00:00Z",
        # homework: 17:00 local the day before
        add("Kodutöö", kind="kodutöö", all_day=1, start_date="2026-10-26"): "2026-10-25T15:00:00Z",
        # 30 minutes before: 03:30 EEST (first pass) and 04:15 EET, whose reminder is 03:45 EET
        # in the second pass
        add("Esimene 03:30", kind="sündmus", start_at="2026-10-25T00:30:00Z"): "2026-10-25T00:00:00Z",
        add("Teine 03:30", kind="sündmus", start_at="2026-10-25T01:30:00Z"): "2026-10-25T01:00:00Z",
        add("Kell 04:15", kind="sündmus", start_at="2026-10-25T02:15:00Z"): "2026-10-25T01:45:00Z",
        add("Esmaspäeva hommik", kind="sündmus", start_at="2026-10-26T06:00:00Z"): "2026-10-26T05:30:00Z",
    }
    drive(conn, clock, utc("2026-10-24T12:00:00"), utc("2026-10-26T12:00:00"))

    sent = [([t for t in fire_at if t in text], at) for text, at in pushed]
    assert all(len(titles) == 1 for titles, _ in sent), pushed
    sent = [(titles[0], at) for titles, at in sent]
    assert sorted(title for title, _ in sent) == sorted(fire_at), "each reminder exactly once"
    for title, at in sent:
        due = utc(fire_at[title][:-1])
        # the job ticks every 60 s plus up to 30 s of jitter
        assert due <= at <= due + timedelta(seconds=91), (title, iso(at))


# --- The ICS feed ---------------------------------------------------------------------------

def parsed_feed(conn):
    cal = Calendar.from_ical(ics.feed(conn))
    [vtz] = cal.walk("VTIMEZONE")
    return cal, vtz.to_tz(lookup_tzid=False)  # the feed's own rules, not the system's


def test_the_feeds_vtimezone_matches_tallinn_around_every_change(conn):
    _, tz = parsed_feed(conn)
    # Every hour from 2025 to the end of 2027 covers three spring and three autumn changes.
    t, end = utc("2025-01-01T00:00:00"), utc("2028-01-01T00:00:00")
    wrong = []
    while t < end:
        if t.astimezone(tz).utcoffset() != t.astimezone(TALLINN).utcoffset():
            wrong.append(iso(t))
        t += timedelta(hours=1)
    assert not wrong
    change = utc("2026-10-25T01:00:00")
    assert (change - timedelta(seconds=1)).astimezone(tz).utcoffset() == timedelta(hours=3)
    assert change.astimezone(tz).utcoffset() == timedelta(hours=2)


@pytest.mark.parametrize("start_at", [
    "2026-10-24T23:30:00Z",  # 02:30 EEST on the 25th
    "2026-10-25T00:30:00Z",  # 03:30 EEST, the first pass
    "2026-10-25T01:30:00Z",  # 03:30 EET, the second pass
    "2026-10-25T02:30:00Z",  # 04:30 EET
    "2026-10-25T08:00:00Z",  # 10:00 EET
])
def test_a_25_10_event_in_the_feed_is_the_same_instant(conn, start_at):
    events.create(conn, {"title": "Sündmus", "status": "kinnitatud", "start_at": start_at,
                         "end_at": iso(utc(start_at[:-1]) + timedelta(minutes=45))})
    cal, tz = parsed_feed(conn)
    [ev] = cal.walk("VEVENT")

    def instant(name):
        value = ev.decoded(name)
        if ev[name].params.get("TZID") is None:
            return value
        assert ev[name].params["TZID"] == "Europe/Tallinn"
        # A client resolves the local time with the feed's VTIMEZONE; RFC 5545 3.3.5: a local
        # time that occurs twice means its first occurrence.
        return value.replace(tzinfo=tz, fold=0)

    start, end = instant("dtstart"), instant("dtend")
    assert iso(start) == start_at
    assert end - start == timedelta(minutes=45)


def test_a_weekly_lesson_over_the_change_keeps_its_local_time_in_the_feed(conn):
    events.create(conn, {"title": "Mate", "kind": "tund", "status": "kinnitatud", "start_at": "2026-10-19T05:00:00Z",
                         "end_at": "2026-10-19T05:45:00Z", "rrule": "FREQ=WEEKLY;COUNT=3"})
    cal, _ = parsed_feed(conn)
    [ev] = cal.walk("VEVENT")
    assert ev["dtstart"].params["TZID"] == "Europe/Tallinn"
    assert ev.decoded("dtstart").replace(tzinfo=None) == datetime(2026, 10, 19, 8, 0)
    # The same 08:00 on 26.10 is 06:00Z, an hour later in UTC than the week before.
    got = events.occurrences(conn, date(2026, 10, 19), date(2026, 10, 27))
    assert [o["start"] for o in got] == ["2026-10-19T08:00", "2026-10-26T08:00"]
