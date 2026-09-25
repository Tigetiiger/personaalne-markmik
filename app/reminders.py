"""Reminders pushed to the phone through ntfy (vault "ntfy" = {url, topic, token?}).

Rules (settings "reminder_rules" overrides the defaults):
  test      kontrolltöö: at `time` local on the evening before
  homework  kodutöö events and dated tasks: at `time` local on the day before they are due
  before    other timed events except lessons (tund): `minutes` before the start

The worker checks every minute. Each (item, start, rule) is sent once: reminders_sent is
claimed before the push, so the repeated 03:00 hour at the end of summer time, a restart or a
second tick cannot send it twice. The occurrence column holds the item's start (UTC ISO, or the
date for all-day items and tasks), so an event moved to another time is reminded again. A
reminder more than GRACE after its event started is dropped.
"""

import json
import re
from datetime import datetime, time, timedelta
from urllib.parse import urlparse

import httpx

from . import connections, events, vault
from .jobs import job
from .util import TALLINN, iso, now_utc, parse_iso

RULES_KEY = "reminder_rules"
# The worker ticks every 60 s plus jitter: a "0 minutes before" reminder still goes out.
GRACE = timedelta(minutes=2)
DEFAULT_URL = "http://100.122.97.7:8090"
DEFAULT_RULES = {
    "test": {"on": True, "time": "18:00"},
    "homework": {"on": True, "time": "17:00"},
    "before": {"on": True, "minutes": 30},
}
WEEKDAYS = ("esmaspäev", "teisipäev", "kolmapäev", "neljapäev", "reede", "laupäev", "pühapäev")


def rules(conn):
    row = conn.execute("SELECT value FROM settings WHERE key = ?", (RULES_KEY,)).fetchone()
    stored = json.loads(row["value"]) if row else {}
    return {name: {**default, **stored.get(name, {})} for name, default in DEFAULT_RULES.items()}


def save_rules(conn, data):
    """Validate and store the rules the settings form sent; returns them. ValueError (Estonian)."""
    if not isinstance(data, dict):
        raise ValueError("Vigased reeglid.")
    out = {}
    for name, default in DEFAULT_RULES.items():
        given = data.get(name, {})
        if not isinstance(given, dict):
            raise ValueError("Vigased reeglid.")
        rule = {**default, **given}
        rule["on"] = bool(rule["on"])
        if "time" in default and not re.fullmatch(r"([01]\d|2[0-3]):[0-5]\d", str(rule["time"])):
            raise ValueError("Kellaaeg peab olema kujul HH:MM.")
        if "minutes" in default:
            if not isinstance(rule["minutes"], int) or isinstance(rule["minutes"], bool) or not 0 <= rule["minutes"] <= 1440:
                raise ValueError("Minuteid peab olema 0 kuni 1440.")
        out[name] = {k: rule[k] for k in default}
    conn.execute(
        "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT (key) DO UPDATE SET value = excluded.value",
        (RULES_KEY, json.dumps(out)),
    )
    return out


def due_reminders(conn, now):
    """Reminders whose time has come at `now` (aware) and that were not sent yet:
    [{item_kind, item_id, occurrence, rule, fire_at, title, message}]."""
    cfg = rules(conn)
    today = now.astimezone(TALLINN).date()
    out = []
    # The earliest reminder is a day before its item (or `minutes` <= 1440 before), so the next
    # three local days cover everything that can be due now.
    for occ in events.occurrences(conn, today, today + timedelta(days=3), statuses=("kinnitatud",)):
        if occ["done"]:
            continue
        day = datetime.fromisoformat(occ["start"][:10]).date()
        starts = datetime.combine(day, time(), TALLINN) if occ["all_day"] else _start_utc(conn, occ)
        if occ.get("task_id"):
            name, kind, item_id = "homework", "task", occ["task_id"]
        elif occ["kind"] == "kontrolltöö":
            name, kind, item_id = "test", "event", occ["id"]
        elif occ["kind"] == "kodutöö":
            name, kind, item_id = "homework", "event", occ["id"]
        elif not occ["all_day"] and occ["kind"] != "tund":
            name, kind, item_id = "before", "event", occ["id"]
        else:
            continue
        rule = cfg[name]
        if not rule["on"]:
            continue
        if name == "before":
            # Absolute time: 30 minutes before 04:15 on 25.10 is 03:45 in the second pass of the
            # repeated hour, not the first.
            fire = starts - timedelta(minutes=rule["minutes"])
        else:
            h, m = map(int, rule["time"].split(":"))
            fire = datetime.combine(day - timedelta(days=1), time(h, m), TALLINN)
        if not (fire <= now < starts + GRACE):
            continue
        key = occ["start"][:10] if occ["all_day"] else iso(starts)
        if conn.execute(
            "SELECT 1 FROM reminders_sent WHERE item_kind = ? AND item_id = ? AND occurrence = ? AND rule = ?",
            (kind, item_id, key, name),
        ).fetchone():
            continue
        title, message = _text(name, occ, day, today, rule)
        out.append({"item_kind": kind, "item_id": item_id, "occurrence": key, "rule": name,
                    "fire_at": iso(fire), "title": title, "message": message})
    return out


def send(conn, reminder):
    """Push one reminder. True when sent now; False when already sent or ntfy is not set up.
    The claim is released again when the push fails, so the next tick retries."""
    cfg = ntfy_config(conn)
    if cfg is None:
        return False
    key = (reminder["item_kind"], reminder["item_id"], reminder["occurrence"], reminder["rule"])
    claimed = conn.execute(
        "INSERT OR IGNORE INTO reminders_sent (item_kind, item_id, occurrence, rule) VALUES (?, ?, ?, ?)", key
    ).rowcount
    if not claimed:
        return False
    try:
        publish(cfg, reminder["title"], reminder["message"])
    except Exception:
        conn.execute(
            "DELETE FROM reminders_sent WHERE item_kind = ? AND item_id = ? AND occurrence = ? AND rule = ?", key
        )
        raise
    return True


def ntfy_config(conn):
    """{url, topic, token} from the vault, or None when no topic is set."""
    cfg = vault.get_json(conn, "ntfy") or {}
    if not cfg.get("topic"):
        return None
    return {"url": (cfg.get("url") or DEFAULT_URL).rstrip("/"), "topic": cfg["topic"], "token": cfg.get("token")}


def publish(cfg, title, message):
    """JSON publishing (POST to the server root), so Estonian letters need no header encoding."""
    headers = {"Authorization": f"Bearer {cfg['token']}"} if cfg.get("token") else {}
    r = httpx.post(cfg["url"], json={"topic": cfg["topic"], "title": title, "message": message, "tags": ["calendar"]},
                   headers=headers, timeout=10)
    if r.status_code >= 400:
        # Not raise_for_status(): keep the message short and free of request details.
        raise RuntimeError(f"ntfy vastas {r.status_code}")


@job("reminders", every=60)
def run(conn):
    if ntfy_config(conn) is None:
        return
    errors = []
    for reminder in due_reminders(conn, now_utc()):
        try:
            send(conn, reminder)
        except Exception as exc:  # one refused push must not hold back the others
            errors.append(exc)
    if errors:
        raise RuntimeError(f"{len(errors)} meeldetuletust jäi saatmata: {errors[0]}")


def _start_utc(conn, occ):
    """Aware UTC start of a timed occurrence. Not from the local string: in the repeated autumn
    hour '03:30' names two instants."""
    if occ["occurrence"]:
        return parse_iso(occ["occurrence"])  # a series occurrence's key is its UTC start
    return parse_iso(conn.execute("SELECT start_at FROM events WHERE id = ?", (occ["id"],)).fetchone()["start_at"])


def _text(name, occ, day, today, rule):
    when = _day_word(day, today)
    clock = occ.get("due_time") or ("" if occ["all_day"] else occ["start"][11:16])
    at = f" kell {clock}" if clock else ""
    where = f", {occ['location']}" if occ.get("location") else ""
    if name == "test":
        return f"{when.capitalize()} kontrolltöö: {occ['title']}", f"{_date_text(day)}{at}{where}"
    if name == "homework":
        label = "Ülesanne" if occ.get("task_id") else "Kodutöö"
        return f"{label} {when}: {occ['title']}", f"Tähtaeg {_date_text(day)}{at}"
    return f"Kell {clock}: {occ['title']}", f"Algab {rule['minutes']} minuti pärast{where}."


def _day_word(day, today):
    if day == today:
        return "täna"
    if day == today + timedelta(days=1):
        return "homme"
    return WEEKDAYS[day.weekday()]


def _date_text(day):
    return f"{WEEKDAYS[day.weekday()]} {day.day}.{day.month}."


def _test_ntfy(conn):
    cfg = ntfy_config(conn)
    if cfg is None:
        return {"ok": False, "summary": "", "error": "Teema on määramata."}
    publish(cfg, "Märkmik", "Testteade: meeldetuletused jõuavad kohale.")
    url = urlparse(cfg["url"])
    server = url.hostname + (f":{url.port}" if url.port else "")  # never user:password@
    return {"ok": True, "summary": f"Testteade saadetud ({server}).", "error": None}


connections.register("ntfy", "ntfy teavitused", order=50, test=_test_ntfy, secret_names=("ntfy",))
