"""Reminders pushed to the phone through ntfy (vault "ntfy" = {url, topic, token?}).

Rules (settings "reminder_rules" overrides the defaults):
  test      kontrolltöö: at `time` local on the evening before
  homework  kodutöö events, all-day tähtaeg events ("Registreeri: …") and dated tasks: at `time`
            local on the day before they are due
  before    other timed events except lessons (tund): `minutes` before the start

Own reminders (table `reminders`, docs/KODU-PLAN.md) are not rules: each is pushed once at its
remind_at, or up to OWN_GRACE later with "(hilinenud)" (worker down, VM restart). Older unsent
ones count as missed.

The worker checks every minute. Each (item, start, rule) is sent once: reminders_sent is
claimed before the push, so the repeated 03:00 hour at the end of summer time, a restart or a
second tick cannot send it twice. The occurrence column holds the item's start (UTC ISO, or the
date for all-day items and tasks), so an event moved to another time is reminded again. A
reminder more than GRACE after its event started is dropped.
"""

import json
import re
from datetime import datetime, time, timedelta, timezone
from urllib.parse import urlparse

import httpx

from . import connections, events, ics, vault
from .jobs import job
from .util import TALLINN, iso, now_utc, parse_iso

RULES_KEY = "reminder_rules"
# The worker ticks every 60 s plus jitter: a "0 minutes before" reminder still goes out.
GRACE = timedelta(minutes=2)
OWN_GRACE = timedelta(hours=12)
OWN_TEXT_CAP = 300
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
        elif occ["kind"] == "kodutöö" or (occ["kind"] == "tähtaeg" and occ["all_day"]):
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
    # The URL is owner-set and gets a bearer token, so it must not be pointed at the metadata
    # endpoint or localhost (SSRF). The tailnet ntfy is allowed; only loopback/link-local are not.
    # Checked and then resolved again by httpx: a DNS-rebinding gap between the two is accepted,
    # since only the owner sets this URL and it may point into the tailnet anyway.
    host = urlparse(cfg["url"]).hostname
    try:
        local = not host or ics.resolves_to_local(host)
    except ValueError:
        raise RuntimeError("ntfy serverit ei leitud") from None
    if local:
        raise RuntimeError("ntfy aadress viitab kohalikule masinale, seda ei kasutata")
    headers = {"Authorization": f"Bearer {cfg['token']}"} if cfg.get("token") else {}
    # follow_redirects stays off: a redirect could otherwise reach a blocked address.
    r = httpx.post(cfg["url"], json={"topic": cfg["topic"], "title": title, "message": message, "tags": ["calendar"]},
                   headers=headers, timeout=10, follow_redirects=False)
    if r.status_code >= 400:
        # Not raise_for_status(): keep the message short and free of request details.
        raise RuntimeError(f"ntfy vastas {r.status_code}")


@job("reminders", every=60)
def run(conn):
    if ntfy_config(conn) is None:
        return
    errors = []
    now = now_utc()
    for reminder in due_reminders(conn, now) + due_own(conn, now):
        try:
            if send(conn, reminder) and reminder["item_kind"] == "reminder":
                conn.execute("UPDATE reminders SET sent_at = ? WHERE id = ?", (iso(now), reminder["item_id"]))
        except Exception as exc:  # one refused push must not hold back the others
            errors.append(exc)
    if errors:
        raise RuntimeError(f"{len(errors)} meeldetuletust jäi saatmata: {errors[0]}")


# --- Own reminders ---------------------------------------------------------------------------

def local_at(text):
    """'2026-10-03T18:00' (local wall time) -> aware UTC datetime. ValueError (Estonian)."""
    try:
        value = datetime.fromisoformat(str(text))
    except ValueError:
        raise ValueError("Vigane aeg: kasuta kuju AAAA-KK-PPTHH:MM.") from None
    if value.tzinfo is not None:
        raise ValueError("Vigane aeg: anna kohalik aeg ilma ajavööndita.")
    return value.replace(tzinfo=TALLINN).astimezone(timezone.utc)


def _clean_text(text):
    text = " ".join(str(text or "").split())
    if not text:
        raise ValueError("Meeldetuletuse tekst on puudu.")
    return text[:OWN_TEXT_CAP]


def create(conn, text, remind_at, *, event_id=None, note_id=None, source="käsitsi"):
    """A new reminder; remind_at is an aware datetime. Returns its id. ValueError (Estonian)."""
    text = _clean_text(text)
    if event_id is not None and events.get(conn, event_id) is None:
        raise ValueError("Sündmust ei leitud.")
    return conn.execute(
        "INSERT INTO reminders (text, remind_at, event_id, note_id, source) VALUES (?, ?, ?, ?, ?)",
        (text, iso(remind_at), event_id, note_id, source)).lastrowid


def get(conn, reminder_id):
    row = conn.execute(f"{_SELECT} WHERE r.id = ?", (reminder_id,)).fetchone()
    return _item(row, now_utc()) if row else None


def update(conn, reminder_id, *, text=None, remind_at=None, done=None):
    """Change a reminder; a new time sends it again. LookupError when there is none."""
    if conn.execute("SELECT 1 FROM reminders WHERE id = ?", (reminder_id,)).fetchone() is None:
        raise LookupError(reminder_id)
    if text is not None:
        conn.execute("UPDATE reminders SET text = ? WHERE id = ?", (_clean_text(text), reminder_id))
    if remind_at is not None:
        conn.execute("UPDATE reminders SET remind_at = ?, sent_at = NULL WHERE id = ?", (iso(remind_at), reminder_id))
    if done is not None:
        conn.execute("UPDATE reminders SET done_at = ? WHERE id = ?", (iso(now_utc()) if done else None, reminder_id))


def remove(conn, reminder_id):
    return conn.execute("DELETE FROM reminders WHERE id = ?", (reminder_id,)).rowcount > 0


# A reminder about a removed event is neither listed nor sent.
_SELECT = """SELECT r.*, n.slug AS note_slug, e.title AS event_title FROM reminders r
             LEFT JOIN notes n ON n.id = r.note_id AND n.deleted_at IS NULL
             LEFT JOIN events e ON e.id = r.event_id"""
_LIVE = "(r.event_id IS NULL OR e.status IS NULL OR e.status != 'eemaldatud')"


def items(conn, start, end, *, missed=False):
    """Reminders between local dates start (inclusive) and end (exclusive), by time; missed: also
    every earlier one never sent and not done (Täna shows those). Local times as the API speaks
    them: remind_at '2026-10-03T18:00'."""
    s_utc = iso(datetime.combine(start, time(), TALLINN))
    e_utc = iso(datetime.combine(end, time(), TALLINN))
    sql = f"{_SELECT} WHERE {_LIVE} AND ((r.remind_at >= ? AND r.remind_at < ?)"
    params = [s_utc, e_utc]
    if missed:
        sql += " OR (r.remind_at < ? AND r.sent_at IS NULL AND r.done_at IS NULL)"
        params.append(iso(now_utc() - OWN_GRACE))
    now = now_utc()
    return [_item(r, now) for r in conn.execute(sql + ") ORDER BY r.remind_at, r.id", params)]


def _item(row, now):
    at = parse_iso(row["remind_at"])
    return {"id": row["id"], "text": row["text"], "remind_at": at.astimezone(TALLINN).strftime("%Y-%m-%dT%H:%M"),
            "event_id": row["event_id"], "event_title": row["event_title"], "note_slug": row["note_slug"],
            "source": row["source"], "done": row["done_at"] is not None, "sent": row["sent_at"] is not None,
            "missed": row["sent_at"] is None and row["done_at"] is None and at + OWN_GRACE < now}


def due_own(conn, now):
    """Own reminders to push at `now`: due, not done, not sent, at most OWN_GRACE late."""
    rows = conn.execute(
        f"""{_SELECT} WHERE {_LIVE} AND r.done_at IS NULL AND r.sent_at IS NULL
            AND r.remind_at <= ? AND r.remind_at > ?""", (iso(now), iso(now - OWN_GRACE))).fetchall()
    out = []
    for r in rows:
        at = parse_iso(r["remind_at"])
        late = "(hilinenud) " if now - at > GRACE else ""
        local = at.astimezone(TALLINN)
        about = f" · {r['event_title']}" if r["event_title"] else ""
        out.append({"item_kind": "reminder", "item_id": r["id"], "occurrence": r["remind_at"], "rule": "at",
                    "fire_at": r["remind_at"], "title": f"{late}{r['text']}",
                    "message": f"Meeldetuletus {_date_text(local.date())} kell {local:%H:%M}{about}"})
    return out


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
        label = "Ülesanne" if occ.get("task_id") else "Tähtaeg" if occ["kind"] == "tähtaeg" else "Kodutöö"
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
