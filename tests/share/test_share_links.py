"""app/shares.py and looking a link up (auth.enter_share)."""

import hashlib
from datetime import datetime, timedelta, timezone

import pytest
from flask import session

from app import auth, shares
from app.util import iso, now_utc


def test_create_stores_only_the_hash_of_a_128_bit_token(conn, note, link):
    row, token = link(note["id"], "view")
    assert len(token) == 22 and token.replace("-", "").replace("_", "").isalnum()  # 16 bytes, base64url
    assert row["token_hash"] == hashlib.sha256(token.encode()).hexdigest()
    assert (row["note_id"], row["mode"], row["expires_at"], row["revoked_at"], row["last_used_at"]) == (note["id"], "view", None, None, None)
    dump = "\n".join(conn.iterdump())
    assert token not in dump
    assert link(note["id"], "view")[1] != token


def test_create_checks_mode_and_expiry(conn, note, link):
    for mode in ("admin", None, ["view"]):
        with pytest.raises(ValueError):
            link(note["id"], mode)
    past = iso(now_utc() - timedelta(minutes=1))
    for bad in ("homme", "2026-13-01T00:00:00Z", "2030-01-01T10:00:00", past, 1700000000, "9999-12-31T23:59:00-14:00"):
        with pytest.raises(ValueError):
            link(note["id"], "edit", bad)
    later = (now_utc() + timedelta(days=7)).astimezone(timezone(timedelta(hours=3)))
    row, _ = link(note["id"], "edit", later.isoformat(timespec="milliseconds"))
    assert row["expires_at"] == iso(later)  # stored as UTC 'YYYY-MM-DDTHH:MM:SSZ'
    assert conn.execute("SELECT count(*) FROM shares").fetchone()[0] == 1


def test_list_leaves_out_revoked_links_and_keeps_expired_ones(conn, note, other, link, expire):
    first, _ = link(note["id"], "view")
    second, _ = link(note["id"], "edit")
    gone, _ = link(note["id"], "edit")
    link(other["id"], "view")
    expire(first["id"])
    shares.revoke(conn, gone["id"])
    assert [r["id"] for r in shares.list_for_note(conn, note["id"])] == [second["id"], first["id"]]


def test_revoke_is_final_and_keeps_its_first_time(conn, note, link):
    row, _ = link(note["id"], "view")
    shares.revoke(conn, row["id"])
    at = shares.get(conn, row["id"])["revoked_at"]
    assert at is not None
    conn.execute("UPDATE shares SET revoked_at = '2020-01-01T00:00:00Z' WHERE id = ?", (row["id"],))
    shares.revoke(conn, row["id"])
    assert shares.get(conn, row["id"])["revoked_at"] == "2020-01-01T00:00:00Z"


def test_lookup_finds_valid_links_only_and_records_their_use(app, conn, note, link, expire):
    valid, token = link(note["id"], "edit")
    revoked, revoked_token = link(note["id"], "edit")
    shares.revoke(conn, revoked["id"])
    expired, expired_token = link(note["id"], "view")
    expire(expired["id"])
    soon = iso(now_utc() + timedelta(seconds=30))
    ending, ending_token = link(note["id"], "view", soon)
    with app.test_request_context("/j/x"):
        for bad in (revoked_token, expired_token, token[:-1], token + "A", "", "x" * 500):
            assert auth.enter_share(conn, bad) is None
            assert "share_id" not in session
        assert auth.enter_share(conn, ending_token)["id"] == ending["id"]
        found = auth.enter_share(conn, token, "  Mari Maasikas  ")
        assert found["id"] == valid["id"] and session["share_id"] == valid["id"]
        assert session["guest_name"] == "Mari Maasikas"
        auth.enter_share(conn, token, "   ")
        assert session["guest_name"] == "Külaline"
    used = shares.get(conn, valid["id"])["last_used_at"]
    assert used is not None and abs(datetime.fromisoformat(used.replace("Z", "+00:00")) - now_utc()) < timedelta(seconds=5)
    assert shares.get(conn, revoked["id"])["last_used_at"] is None


def test_expiry_takes_effect_by_itself(app, conn, note, link):
    row, token = link(note["id"], "view", iso(now_utc() + timedelta(seconds=30)))
    conn.execute("UPDATE shares SET expires_at = ? WHERE id = ?", (iso(now_utc() - timedelta(seconds=1)), row["id"]))
    with app.test_request_context("/j/x"):
        assert auth.enter_share(conn, token) is None
