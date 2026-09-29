"""The night (docs/AKTSIAD-PLAN.md, "Nightly and daytime jobs"), rewritten from stock-oracle's
nightly.py on Märkmik's job registry and a thread of its own:

    prices     bars, corporate actions and FX (market.refresh_prices)
    ingest     news and filings (sources.ingest)
    attention  document counts behind the attention alert (sources.count_docs)
    extract    the model reads the new documents        } from 02:00, until 06:00;
    research   deep research on the deep-research days  } the AI package (research.py,
    verdict    would buy / hold / sell                  } verdict.py)
    notes      one note per stock in the brain repo (notes.py)
    digest     the morning digest, stored for the 07:00 job (alerts.py)

One chained run rather than a job per step, so no step starts before the one it reads from has
finished. Each step is recorded in stock_runs.steps_json; a step that raises is recorded as failed
and the night carries on. A step whose detail reports failures inside it (a source down, a stock
skipped) is "partial".

The AI steps wait for 02:00 when the night got there early and stop at 06:00 (`deadline`): the
subscription is the owner's again in the morning. A run started by hand after 06:00 of its date
gets MANUAL_BUDGET from its start instead, so the button and the CLI can still run the AI steps.
When a step's detail says `stopped` (the usage limit, the night's cap, the deadline), the AI steps
after it are skipped and recorded with that reason.

Rerunning a date reuses its stock_runs row: steps that succeeded are not run again, except the
notes and the digest, which summarise the others; everything else is idempotent (upserts,
deduplicated documents, the AI package skips stocks already done for the date).

    python -m app.stocks.night --date 2026-09-30 [--step ingest]
"""

import argparse
import importlib
import json
import logging
import os
import sys
import threading
import time as _time
from datetime import date, datetime, time, timedelta, timezone

from ..ai import runner
from ..db import connect, init_db, tx
from ..util import TALLINN, iso, local_today, now_iso, now_utc
from . import alerts, common, config, market, notes, sources

log = logging.getLogger("markmik.stocks.night")

STEPS = ("prices", "ingest", "attention", "extract", "research", "verdict", "notes", "digest")
AI_STEPS = ("extract", "research", "verdict")
# Summaries of the other steps: a rerun always writes them again.
ALWAYS = ("notes", "digest")
START = time(1, 30)
AI_START = time(2, 0)
DEADLINE = time(6, 0)
MANUAL_BUDGET = timedelta(hours=4)
# The claim goes stale after 30 minutes (common.claim) and the night takes hours: a heartbeat
# keeps it fresh, so the Allikad button and the hourly filings poll see the night running.
HEARTBEAT = 300
CLAIM = "night"
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

NO_AI = "AI pakett puudub"
NOT_DEEP = "Pole süvauuringu öö"
PAST_DEADLINE = "Öö aeg sai läbi"
MISSED = "Öö jäi vahele: töötaja ei käinud kell 01.30 ja kell 06.00 oli möödas."

# Detail keys that mean something inside the step went wrong.
_PROBLEM_KEYS = ("failed", "errors", "stopped")

# Tests replace it: a night that got to the AI steps early sleeps here until 02:00.
_sleep = _time.sleep


class Skipped(Exception):
    """A step that did not run; its message is the reason."""


def at(day, clock):
    """Tallinn wall-clock `clock` on `day` as aware UTC. In UTC because aware datetimes of one
    zone compare by wall clock, which runs back in the repeated autumn hour."""
    return datetime.combine(day, clock, tzinfo=TALLINN).astimezone(timezone.utc)


def has_problems(detail):
    """True when a step's detail reports failures anywhere inside it."""
    if isinstance(detail, dict):
        for key, value in detail.items():
            if key in _PROBLEM_KEYS and value:
                return True
            if key == "status" and value in ("failed", "partial"):
                return True
            if has_problems(value):
                return True
    elif isinstance(detail, list):
        return any(has_problems(v) for v in detail)
    return False


def overall(steps):
    """The run's status from its steps: failed when every step that ran failed, partial when any
    failed or reported problems (a skip for a stop counts), else succeeded."""
    ran = [s for s in steps.values() if isinstance(s, dict)]
    if ran and all(s["status"] == "failed" for s in ran):
        return "failed"
    if any(s["status"] in ("failed", "partial") or has_problems(s.get("detail")) for s in ran):
        return "partial"
    return "succeeded"


class _Night:
    def __init__(self, conn, run_date, run_id, clock):
        self.conn, self.run_date, self.run_id, self.clock = conn, run_date, run_id, clock
        started = clock()
        night_end = at(run_date, DEADLINE)
        self.deadline = night_end if started < night_end else started + MANUAL_BUDGET
        self.stopped = None
        self.waited = False

    def ai(self, name):
        """The AI package's module, or Skipped when the night may not (or cannot) use it."""
        if self.stopped:
            raise Skipped(self.stopped)
        try:
            module = importlib.import_module(f"{__package__}.{name}")
        except ModuleNotFoundError as exc:
            if exc.name != f"{__package__}.{name}":
                raise
            raise Skipped(NO_AI) from None
        self._wait()
        if self.clock() >= self.deadline:
            self.stopped = PAST_DEADLINE
            raise Skipped(PAST_DEADLINE)
        return module

    def _wait(self):
        if self.waited:
            return
        self.waited = True
        left = (at(self.run_date, AI_START) - self.clock()).total_seconds()
        # Only a night that is really early waits; a run by hand at noon for tomorrow does not
        # sleep until the small hours.
        early = (at(self.run_date, AI_START) - at(self.run_date, START)).total_seconds()
        if 0 < left <= early:
            log.info("night %s: waiting %.0f s for 02:00", self.run_date, left)
            _sleep(left)

    def ai_done(self, detail):
        if isinstance(detail, dict) and detail.get("stopped"):
            self.stopped = detail["stopped"]
        return detail


def step_prices(n):
    return market.refresh_prices(n.conn, n.run_date)


def step_ingest(n):
    return sources.ingest(n.conn, n.clock(), run_id=n.run_id)


def step_attention(n):
    counted = sources.count_docs(n.conn, n.run_date)
    z = {}
    for row in common.followed(n.conn):
        value = sources.attention_z(n.conn, row["id"], n.run_date)
        if value is not None:
            z[row["symbol"]] = round(value, 2)
    return {**counted, "z": z}


def step_extract(n):
    research = n.ai("research")
    return n.ai_done(research.extract_night(n.conn, n.run_id, n.clock, n.deadline))


def step_research(n):
    if WEEKDAYS[n.run_date.weekday()] not in config.settings(n.conn)["deep_days"]:
        raise Skipped(NOT_DEEP)
    research = n.ai("research")
    return n.ai_done(research.deep_night(n.conn, n.run_id, n.run_date, n.clock, n.deadline))


def step_verdict(n):
    verdict = n.ai("verdict")
    return n.ai_done(verdict.verdict_night(n.conn, n.run_id, n.run_date, n.clock, n.deadline))


def step_notes(n):
    out = {"written": [], "unchanged": 0, "busy": [], "failed": {}}
    for row in common.followed(n.conn):
        try:
            result = notes.update(n.conn, row["id"], n.run_date)
        except Exception as exc:
            log.exception("night %s: note of %s", n.run_date, row["symbol"])
            if n.conn.in_transaction:
                n.conn.execute("ROLLBACK")
            out["failed"][row["symbol"]] = f"{type(exc).__name__}: {exc}"[:500]
            continue
        if result == notes.BUSY:
            out["busy"].append(row["symbol"])
        elif result == notes.UNCHANGED:
            out["unchanged"] += 1
        else:
            out["written"].append(row["symbol"])
    return out


def step_digest(n):
    steps = _steps(n.conn, n.run_id)
    steps.pop("digest", None)
    return alerts.store_digest(n.conn, n.run_date, status=overall(steps))


STEP_FUNCS = {"prices": step_prices, "ingest": step_ingest, "attention": step_attention, "extract": step_extract,
              "research": step_research, "verdict": step_verdict, "notes": step_notes, "digest": step_digest}


def run(conn, run_date, *, now=None, only=None):
    """The night for `run_date` (a Tallinn date) on this thread; returns its stock_runs.id.
    `now`: aware UTC datetime or a callable returning one (tests); `only`: one step's name."""
    if only is not None and only not in STEPS:
        raise ValueError(f"unknown step: {only}")
    clock = now if callable(now) else (lambda: now) if now is not None else now_utc
    run_id, steps = _open(conn, run_date, clock())
    night = _Night(conn, run_date, run_id, clock)
    for name in STEPS:
        if only is not None and name != only:
            continue
        before = steps.get(name)
        if only is None and name not in ALWAYS and isinstance(before, dict) and before.get("status") == "succeeded":
            continue
        started = clock()
        try:
            detail = STEP_FUNCS[name](night)
            status = "partial" if has_problems(detail) else "succeeded"
        except Skipped as skip:
            reason = str(skip)
            detail = {"reason": reason}
            if name in AI_STEPS and night.stopped == reason:
                detail["stopped"] = reason  # skipped because the night stopped: a problem
            status = "skipped"
        except Exception as exc:
            log.exception("night %s: step %s failed", run_date, name)
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            detail = {"error": f"{type(exc).__name__}: {exc}"[:2000]}
            if name in AI_STEPS and isinstance(exc, runner.AIUnavailable):
                # The contract says the AI steps return this as `stopped`; should one raise it
                # instead, the night still starts no further AI step.
                night.stopped = detail["stopped"] = str(exc)[:500] or "AI pole kättesaadav"
            status = "failed"
        steps[name] = {"status": status, "started_at": iso(started), "finished_at": iso(clock()), "detail": detail}
        _save(conn, run_id, steps)
    status = overall(steps)
    conn.execute("UPDATE stock_runs SET status = ?, finished_at = ? WHERE id = ?", (status, iso(clock()), run_id))
    if only is None:
        try:
            if status != "succeeded":
                alerts.night_alert(conn, run_date, status, steps, clock())
            alerts.check(conn, clock())
        except Exception:
            log.exception("night %s: alerts not sent", run_date)
    log.info("night %s: %s", run_date, status)
    return run_id


def _open(conn, run_date, now):
    with tx(conn):
        row = conn.execute("SELECT id, steps_json FROM stock_runs WHERE run_date = ? ORDER BY id DESC LIMIT 1",
                           (run_date.isoformat(),)).fetchone()
        if row is None:
            run_id = conn.execute("INSERT INTO stock_runs (run_date, started_at, status) VALUES (?, ?, 'running')",
                                  (run_date.isoformat(), iso(now))).lastrowid
            return run_id, {}
        conn.execute("UPDATE stock_runs SET started_at = ?, finished_at = NULL, status = 'running' WHERE id = ?",
                     (iso(now), row["id"]))
        steps = json.loads(row["steps_json"] or "{}")
        # A skipped night's marker is not a step of the run that now happens after all.
        steps.pop("night", None)
        return row["id"], steps


def _steps(conn, run_id):
    return json.loads(conn.execute("SELECT steps_json FROM stock_runs WHERE id = ?", (run_id,)).fetchone()["steps_json"])


def _save(conn, run_id, steps):
    conn.execute("UPDATE stock_runs SET steps_json = ? WHERE id = ?", (json.dumps(steps, ensure_ascii=False, default=str), run_id))


def has_run(conn, run_date):
    return conn.execute("SELECT 1 FROM stock_runs WHERE run_date = ?", (run_date.isoformat(),)).fetchone() is not None


def record_skipped(conn, run_date, now, reason=MISSED):
    """A night that never ran (the VM was off from 01:30 until after 06:00), recorded and alerted
    once. False when the date already has a run."""
    with tx(conn):
        if has_run(conn, run_date):
            return False
        steps = {"night": {"status": "skipped", "started_at": iso(now), "finished_at": iso(now), "detail": {"reason": reason}}}
        conn.execute("INSERT INTO stock_runs (run_date, started_at, finished_at, status, steps_json) VALUES (?, ?, ?, 'skipped', ?)",
                     (run_date.isoformat(), iso(now), iso(now), json.dumps(steps, ensure_ascii=False)))
    try:
        alerts.night_alert(conn, run_date, "skipped", steps, now)
    except Exception:
        log.exception("night %s: skipped-night alert not sent", run_date)
    return True


# --- On a thread of its own ---------------------------------------------------------------------

def start(db_path, run_date):
    """Claim the night and run it on a thread with its own connection, so the worker's other jobs
    (reminders every minute) never wait for it. False when a night is already running."""
    conn = connect(db_path)
    if not common.claim(conn, CLAIM):
        conn.close()
        return False
    threading.Thread(target=_background, args=(db_path, conn, run_date), name="stocks-night", daemon=True).start()
    return True


def _background(db_path, conn, run_date):
    try:
        _claimed_run(db_path, conn, run_date)
    except Exception:
        log.exception("night %s failed", run_date)
    finally:
        conn.close()


def _claimed_run(db_path, conn, run_date, only=None):
    """run() under a claim already taken; the claim is kept fresh meanwhile and always released."""
    stop = threading.Event()
    beat = threading.Thread(target=_heartbeat, args=(db_path, stop), name="stocks-night-claim", daemon=True)
    beat.start()
    try:
        return run(conn, run_date, only=only)
    finally:
        stop.set()
        beat.join()
        common.release(conn, CLAIM)


def _heartbeat(db_path, stop):
    conn = connect(db_path)
    try:
        while not stop.wait(HEARTBEAT):
            conn.execute("UPDATE settings SET value = ? WHERE key = ?", (now_iso(), f"stocks:running:{CLAIM}"))
    except Exception:
        log.exception("night claim heartbeat stopped")
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m app.stocks.night")
    parser.add_argument("--date", type=date.fromisoformat, default=None, help="the night's Tallinn date YYYY-MM-DD (default today)")
    parser.add_argument("--step", choices=STEPS, default=None, help="run only this step")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    for name in ("httpx", "httpcore"):  # request URLs may carry API keys
        logging.getLogger(name).setLevel(logging.WARNING)
    db_path = os.environ.get("DATABASE_PATH", "data/markmik.db")
    init_db(db_path)
    run_date = args.date or local_today()
    conn = connect(db_path)
    try:
        if not common.claim(conn, CLAIM):
            print("Öö juba käib.", file=sys.stderr)
            return 2
        run_id = _claimed_run(db_path, conn, run_date, only=args.step)
        status = conn.execute("SELECT status FROM stock_runs WHERE id = ?", (run_id,)).fetchone()["status"]
    finally:
        conn.close()
    print(f"öö {run_date}: {status}")
    return 0 if status == "succeeded" else 1


if __name__ == "__main__":
    raise SystemExit(main())
