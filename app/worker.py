"""Background worker: runs registered jobs (app/jobs.py) on schedule. One instance only
(file lock). Start: python -m app.worker. Stops cleanly on SIGTERM/SIGINT."""

import fcntl
import logging
import os
import signal
import threading
from datetime import timedelta
from pathlib import Path

from . import jobs
from .db import connect, init_db
from .util import iso, now_utc, parse_iso

log = logging.getLogger("markmik.worker")


def load_job_modules():
    from . import import_plugins
    import_plugins()


def _get_setting(conn, key):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else None


def _set_setting(conn, key, value):
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (key, value),
    )


def run_due(conn, now):
    """Run every job that is due at `now`. Returns the time of the next due job."""
    soonest = now + timedelta(seconds=30)
    for j in jobs.REGISTRY.values():
        last_text = _get_setting(conn, f"job:{j.name}:last")
        last = parse_iso(last_text) if last_text else None
        due = parse_iso(_get_setting(conn, f"job:{j.name}:next") or iso(jobs.next_run(j, last, now)))
        if due <= now:
            try:
                j.fn(conn)
                _set_setting(conn, f"job:{j.name}:error", "")
            except Exception as exc:
                log.exception("job %s failed", j.name)
                _set_setting(conn, f"job:{j.name}:error", f"{now_utc().isoformat()} {exc}")
            if conn.in_transaction:  # a job that raised inside tx() must not leave it open
                conn.execute("ROLLBACK")
            _set_setting(conn, f"job:{j.name}:last", iso(now))
            due = jobs.next_run(j, now, now)
            _set_setting(conn, f"job:{j.name}:next", iso(due))
        soonest = min(soonest, due)
    return soonest


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    # httpx logs every request URL at INFO; feed and login URLs can carry tokens.
    for name in ("httpx", "httpcore"):
        logging.getLogger(name).setLevel(logging.WARNING)
    db_path = os.environ.get("DATABASE_PATH", "data/markmik.db")
    init_db(db_path)
    lock_file = open(Path(db_path).with_name("worker.lock"), "w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        raise SystemExit("another worker is already running")
    load_job_modules()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    conn = connect(db_path)
    log.info("worker started with jobs: %s", ", ".join(sorted(jobs.REGISTRY)) or "(none)")
    while not stop.is_set():
        soonest = run_due(conn, now_utc())
        stop.wait(max(1.0, min(30.0, (soonest - now_utc()).total_seconds())))
    conn.close()


if __name__ == "__main__":
    main()
