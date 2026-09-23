"""STUB (Wave 0). WP-A replaces this whole file; signatures in docs/CONTRACTS.md §4 collab."""


def is_busy(conn, note_id):
    return False


def close_all(conn, note_id):
    conn.execute("UPDATE collab_sessions SET closed = 1 WHERE note_id = ? AND closed = 0", (note_id,))
