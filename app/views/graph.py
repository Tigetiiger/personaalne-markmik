"""Link graph page /graaf and /api/graph.

Owner: WP-I (graph); see docs/CONTRACTS.md."""

import json
import os

from flask import Blueprint, jsonify, render_template, request

from ..db import get_db, tx
from ..graph import graph

bp = Blueprint("graph", __name__)

TEST_PAGES = os.environ.get("MARKMIK_TEST_PAGES") == "1"


@bp.get("/graaf")
def page():
    return render_template("graph.html", test_pages=TEST_PAGES)


@bp.get("/api/graph")
def api_graph():
    args = request.args
    return jsonify(graph(get_db(), folder=args.get("folder") or None, tag=args.get("tag") or None,
                         orphans=args.get("orphans", "1") != "0"))


if TEST_PAGES:
    @bp.post("/_test/graph/seed")
    def seed():
        """Browser tests: replace every note with {"notes": [{slug, title?, folder?, tags?,
        is_daily?}], "links": [[src_slug, dst_slug], ...]}."""
        data = request.get_json()
        conn = get_db()
        with tx(conn):
            conn.execute("DELETE FROM notes")
            conn.executemany(
                "INSERT INTO notes (slug, title, folder, tags_json, is_daily) VALUES (?, ?, ?, ?, ?)",
                [(n["slug"], n.get("title", n["slug"]), n.get("folder", ""), json.dumps(n.get("tags", [])),
                  int(n.get("is_daily", False))) for n in data["notes"]],
            )
            ids = {r["slug"]: r["id"] for r in conn.execute("SELECT id, slug FROM notes")}
            conn.executemany("INSERT INTO links (src_id, dst_slug, dst_id) VALUES (?, ?, ?)",
                             [(ids[src], dst, ids.get(dst)) for src, dst in data["links"]])
        return "", 204
