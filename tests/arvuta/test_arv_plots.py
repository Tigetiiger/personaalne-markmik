"""Plots: stored re-encoded under the note, written into the output region, cleaned up at night."""

import base64
import io

from PIL import Image

from app import images, notes
from app.arvuta import plots
from app.views import arvuta as view


def png(size=(40, 30)):
    out = io.BytesIO()
    Image.new("RGB", size, "blue").save(out, "PNG", pnginfo=None)
    return base64.b64encode(out.getvalue()).decode()


def test_store_reencodes_and_refuses_what_is_not_a_png(app, conn):
    n = notes.create(conn, "Graafik")
    with app.app_context():
        name = plots.store(conn, png(), n["id"])
        jpeg = io.BytesIO()
        Image.new("RGB", (5, 5)).save(jpeg, "JPEG")
        assert plots.store(conn, base64.b64encode(jpeg.getvalue()).decode(), n["id"]) is None
        assert plots.store(conn, base64.b64encode(b"<svg onload=x>").decode(), n["id"]) is None
        assert plots.store(conn, "ei ole base64!!", n["id"]) is None
        assert plots.store(conn, png((5000, 5000)), n["id"]) is None
    assert name and name.endswith(".png")
    assert conn.execute("SELECT author FROM images WHERE name = ?", (name,)).fetchone()["author"] == "arvutus"
    assert images.allowed(conn, n["id"], name)


def test_a_run_writes_the_plot_into_the_region(owner, conn, monkeypatch):
    n = notes.create(conn, "Joonis", body="# Joonis\n\n```arvuta\nplt.plot([1])\n```\n")
    monkeypatch.setattr(view.client, "send", lambda req, s: {
        "cells": [{"out": "", "error": None, "tables": [], "images": [png()]}], "tables": [], "inline": []})
    r = owner.post("/api/arvuta", json={"slug": n["slug"], "markdown": n["body"], "run": "all"})
    [name] = conn.execute("SELECT name FROM images WHERE author = 'arvutus'").fetchall()
    assert r.json["markdown"].endswith(f"```arvuta\nplt.plot([1])\n```\n\n![väljund](/pildid/{name[0]})\n")


def test_clean_keeps_shown_new_and_uploaded_pictures(app, conn, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    with app.app_context():
        shown = plots.store(conn, png((10, 10)), None)
        gone = plots.store(conn, png((11, 11)), None)
        fresh = plots.store(conn, png((12, 12)), None)
        upload = images.store(conn, base64.b64decode(png((13, 13))), "minu.png", "omanik").rsplit("/", 1)[1]
    notes.create(conn, "Näitab", body=f"![väljund](/pildid/{shown})")
    conn.execute("UPDATE images SET created_at = '2020-01-01T00:00:00Z' WHERE name != ?", (fresh,))
    plots.clean(conn)
    left = {r["name"] for r in conn.execute("SELECT name FROM images")}
    assert left == {shown, fresh, upload}
    assert not (tmp_path / "pildid" / gone).exists() and (tmp_path / "pildid" / shown).exists()
