"""Response times with a vault of realistic size: 1000 notes with links and tasks, 5000 events of
which a tenth recur. Seeding takes a while, so this runs only with MARKMIK_PERF=1:

    MARKMIK_PERF=1 uv run pytest tests/perf -s

Times are measured through the Flask test client (the app's own work, no network) and printed."""

import os
import random
import statistics
import time
from datetime import datetime, time as dtime, timedelta

import pytest

from app import events, notes, util

pytestmark = pytest.mark.skipif(os.environ.get("MARKMIK_PERF") != "1", reason="set MARKMIK_PERF=1 to run")

NOTES = 1000
EVENTS = 5000
LIMIT_MS = 500  # per request, median of several runs, on the workstation VM
WORDS = ("matemaatika", "füüsika", "ajalugu", "kodutöö", "projekt", "retsept", "reis", "raamat",
         "koosolek", "idee", "trenn", "eksam", "kontrolltöö", "essee", "muusika", "kino")
FOLDERS = ("", "kool", "kool/matemaatika", "kool/füüsika", "isiklik", "isiklik/reisid", "töö")
RULES = ("FREQ=WEEKLY;BYDAY=MO", "FREQ=WEEKLY;BYDAY=TU,TH", "FREQ=DAILY", "FREQ=MONTHLY;BYMONTHDAY=15",
         "FREQ=WEEKLY;INTERVAL=2;BYDAY=FR", "FREQ=YEARLY")


def seed(conn, today):
    rnd = random.Random(7)
    made = [notes.create(conn, f"Märge {i} {rnd.choice(WORDS)}", folder=rnd.choice(FOLDERS)) for i in range(NOTES)]
    for note in made:
        others = rnd.sample(made, 4)
        lines = [f"# {note['title']}", "", " ".join(rnd.choices(WORDS, k=40)) + ".", ""]
        lines += [f"Vaata ka [{o['title']}](/m/{o['slug']})." for o in others]
        lines.append("")
        for t in range(3):
            due = today + timedelta(days=rnd.randint(-10, 30))
            lines.append(f"- [{rnd.choice(' x')}] Ülesanne {t} {rnd.choice(WORDS)} @{due.isoformat()}")
        notes.write_body(conn, note["id"], "\n".join(lines) + "\n")
    for i in range(EVENTS):
        day = today + timedelta(days=rnd.randint(-365, 365))
        start = datetime.combine(day, dtime(rnd.randint(7, 20), rnd.choice((0, 15, 30, 45))), util.TALLINN)
        fields = {"title": f"Sündmus {i} {rnd.choice(WORDS)}", "kind": rnd.choice(events.KINDS),
                  "start_at": util.iso(start), "end_at": util.iso(start + timedelta(minutes=45))}
        if i % 10 == 0:
            fields["rrule"] = rnd.choice(RULES)
        events.create(conn, fields)


def median_ms(owner, url, runs=5):
    r = owner.get(url)  # warm-up: first-request caches and SQLite pages
    assert r.status_code == 200, (url, r.data[:200])
    times = []
    for _ in range(runs):
        t0 = time.perf_counter()
        r = owner.get(url)
        times.append((time.perf_counter() - t0) * 1000)
        assert r.status_code == 200, url
    return statistics.median(times), r.get_json()


def test_endpoints_are_fast_with_a_full_vault(owner, conn):
    today = util.local_today()
    t0 = time.perf_counter()
    seed(conn, today)
    print(f"\nseeded {NOTES} notes and {EVENTS} events in {time.perf_counter() - t0:.1f} s")
    month = today.replace(day=1)
    next_month = (month + timedelta(days=32)).replace(day=1)
    urls = {
        "notes tree": "/api/notes/tree",
        "search (common word)": "/api/search?q=matemaatika",
        "search (two words)": "/api/search?q=kodu%20projekt",
        "graph": "/api/graph",
        "events, this month": f"/api/events?start={month}&end={next_month}",
        "today": "/api/today",
    }
    results = {}
    for name, url in urls.items():
        ms, data = median_ms(owner, url)
        results[name] = ms
        size = len(data) if isinstance(data, list) else len(data.get("nodes", data))
        print(f"{name:24} {ms:7.1f} ms  ({size} items)")
    slow = {name: round(ms) for name, ms in results.items() if ms > LIMIT_MS}
    assert not slow, f"over {LIMIT_MS} ms: {slow}"
