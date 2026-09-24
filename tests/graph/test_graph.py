"""Link graph: nodes and edges from live notes and the links table, filters, /api/graph."""

import json

from app.graph import graph


def note(conn, slug, *, folder="", tags=(), is_daily=False, deleted=False):
    cur = conn.execute(
        "INSERT INTO notes (slug, title, folder, tags_json, is_daily, deleted_at) VALUES (?, ?, ?, ?, ?, ?)",
        (slug, slug.upper(), folder, json.dumps(list(tags)), int(is_daily), "2026-09-01T00:00:00Z" if deleted else None),
    )
    return cur.lastrowid


def link(conn, src, dst_slug):
    """src -> dst_slug the way notes.on_saved records it: dst_id is NULL for a missing note."""
    row = conn.execute("SELECT id FROM notes WHERE slug = ?", (dst_slug,)).fetchone()
    conn.execute("INSERT INTO links (src_id, dst_slug, dst_id) VALUES (?, ?, ?)", (src, dst_slug, row["id"] if row else None))


def degrees(result):
    return {n["slug"]: n["degree"] for n in result["nodes"]}


def edge_pairs(conn, result):
    slug = {r["id"]: r["slug"] for r in conn.execute("SELECT id, slug FROM notes")}
    return sorted((slug[e["source"]], slug[e["target"]]) for e in result["edges"])


def test_nodes_carry_the_contract_fields(conn):
    a = note(conn, "a", folder="kool", tags=["mat"])
    note(conn, "b")
    link(conn, a, "b")
    node = next(n for n in graph(conn)["nodes"] if n["slug"] == "a")
    assert node == {"id": a, "slug": "a", "title": "A", "folder": "kool", "degree": 1, "tags": ["mat"]}


def test_degree_counts_in_and_out(conn):
    a, b, c = note(conn, "a"), note(conn, "b"), note(conn, "c")
    link(conn, a, "b")
    link(conn, a, "c")
    link(conn, b, "a")
    result = graph(conn)
    assert degrees(result) == {"a": 3, "b": 2, "c": 1}
    assert edge_pairs(conn, result) == [("a", "b"), ("a", "c"), ("b", "a")]


def test_duplicate_links_and_self_links_make_no_extra_edges(conn):
    a, _ = note(conn, "a"), note(conn, "b")
    link(conn, a, "b")
    link(conn, a, "b")
    link(conn, a, "a")
    result = graph(conn)
    assert edge_pairs(conn, result) == [("a", "b")]
    assert degrees(result) == {"a": 1, "b": 1}


def test_deleted_notes_and_missing_targets_are_left_out(conn):
    a = note(conn, "a")
    gone = note(conn, "gone", deleted=True)
    note(conn, "b")
    link(conn, a, "gone")
    link(conn, gone, "b")
    link(conn, a, "puudub")
    result = graph(conn)
    assert degrees(result) == {"a": 0, "b": 0}
    assert result["edges"] == []


def test_folder_filter_includes_subfolders_only(conn):
    a = note(conn, "a", folder="kool")
    b = note(conn, "b", folder="kool/matemaatika")
    note(conn, "c", folder="koolitus")
    note(conn, "d")
    link(conn, a, "d")
    link(conn, b, "a")
    result = graph(conn, folder="kool")
    # The edge to d leaves the folder, so it is not drawn and does not count.
    assert degrees(result) == {"a": 1, "b": 1}
    assert degrees(graph(conn, folder="/kool/matemaatika/")) == {"b": 0}


def test_tag_filter(conn):
    a = note(conn, "a", tags=["mat", "kool"])
    note(conn, "b", tags=["kool"])
    note(conn, "c", tags=["matemaatika"])
    link(conn, a, "b")
    assert degrees(graph(conn, tag="kool")) == {"a": 1, "b": 1}
    assert degrees(graph(conn, tag="mat")) == {"a": 0}
    assert degrees(graph(conn, folder="x", tag="kool")) == {}


def test_orphans_flag(conn):
    a = note(conn, "a")
    note(conn, "b")
    note(conn, "yksik")
    link(conn, a, "b")
    assert set(degrees(graph(conn))) == {"a", "b", "yksik"}
    assert set(degrees(graph(conn, orphans=False))) == {"a", "b"}


def test_daily_notes_only_when_linked(conn):
    a = note(conn, "a")
    day = note(conn, "2026-09-24", is_daily=True)
    note(conn, "2026-09-23", is_daily=True)
    link(conn, day, "a")
    assert degrees(graph(conn)) == {"a": 1, "2026-09-24": 1}
    # Its only link leaves the folder: now it would be a lone daily note.
    conn.execute("UPDATE notes SET folder = 'paevik' WHERE is_daily = 1")
    assert degrees(graph(conn, folder="paevik")) == {}


def test_api(owner, conn):
    a = note(conn, "a", folder="kool")
    note(conn, "b")
    note(conn, "yksik", folder="kool")
    link(conn, a, "b")
    r = owner.get("/api/graph")
    assert r.status_code == 200
    assert {n["slug"] for n in r.json["nodes"]} == {"a", "b", "yksik"}
    assert r.json["edges"] == [{"source": a, "target": conn.execute("SELECT id FROM notes WHERE slug = 'b'").fetchone()["id"]}]
    assert {n["slug"] for n in owner.get("/api/graph?folder=kool&tag=&orphans=0").json["nodes"]} == set()
    assert {n["slug"] for n in owner.get("/api/graph?folder=kool&orphans=1").json["nodes"]} == {"a", "yksik"}
    assert {n["slug"] for n in owner.get("/api/graph?orphans=0").json["nodes"]} == {"a", "b"}


def test_page(owner):
    r = owner.get("/graaf")
    assert r.status_code == 200
    assert b'data-page="graph"' in r.data and b'id="graaf"' in r.data


def test_owner_only(client):
    assert client.get("/api/graph", headers={"Accept": "application/json"}).status_code == 401
    r = client.get("/graaf", headers={"Accept": "text/html"})
    assert r.status_code == 302 and "/login" in r.headers["Location"]
