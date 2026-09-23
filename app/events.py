"""STUB (Wave 0). WP-D replaces this whole file; signatures in docs/CONTRACTS.md §4 events."""

import uuid


def create(conn, fields):
    fields = {"uid": f"markmik-{uuid.uuid4()}", **fields}
    cols = ", ".join(fields)
    marks = ", ".join("?" for _ in fields)
    return conn.execute(f"INSERT INTO events ({cols}) VALUES ({marks})", tuple(fields.values())).lastrowid


def upsert_by_uid(conn, uid, fields):
    if conn.execute("SELECT 1 FROM rejected_uids WHERE uid = ?", (uid,)).fetchone():
        return None, False
    row = conn.execute("SELECT id, user_edited FROM events WHERE uid = ?", (uid,)).fetchone()
    if row is None:
        return create(conn, {**fields, "uid": uid}), True
    if not row["user_edited"]:
        sets = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE events SET {sets} WHERE id = ?", (*fields.values(), row["id"]))
    return row["id"], False


def remove(conn, event_id):
    row = conn.execute("SELECT uid FROM events WHERE id = ?", (event_id,)).fetchone()
    conn.execute("UPDATE events SET status = 'eemaldatud' WHERE id = ?", (event_id,))
    if row:
        conn.execute("INSERT OR IGNORE INTO rejected_uids (uid) VALUES (?)", (row["uid"],))


def occurrences(conn, start, end, *, statuses=("kinnitatud", "pakutud")):
    return []
