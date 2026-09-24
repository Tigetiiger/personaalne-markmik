"""The link graph behind /graaf: live notes are nodes, links between live notes are edges."""

import json


def graph(conn, *, folder=None, tag=None, orphans=True):
    """{"nodes": [{id, slug, title, folder, degree, tags}], "edges": [{source, target}]}.

    A link is an edge when both ends are live notes and differ; a->b counts once, and b->a is a
    separate edge. The folder and tag filters pick the nodes first (a folder includes its
    subfolders); degree is then counted over the edges among the picked nodes, so node size and
    "orphan" match what is drawn. Daily notes are left out unless they have an edge there.
    """
    folder = (folder or "").strip("/")
    nodes = {}
    for row in conn.execute("SELECT id, slug, title, folder, tags_json, is_daily FROM notes WHERE deleted_at IS NULL ORDER BY id"):
        tags = json.loads(row["tags_json"])
        if folder and row["folder"] != folder and not row["folder"].startswith(folder + "/"):
            continue
        if tag and tag not in tags:
            continue
        nodes[row["id"]] = {"id": row["id"], "slug": row["slug"], "title": row["title"], "folder": row["folder"],
                            "degree": 0, "tags": tags, "is_daily": row["is_daily"]}

    edges = []
    for src, dst in conn.execute("SELECT DISTINCT src_id, dst_id FROM links WHERE dst_id IS NOT NULL AND src_id != dst_id ORDER BY src_id, dst_id"):
        if src in nodes and dst in nodes:
            edges.append({"source": src, "target": dst})
            nodes[src]["degree"] += 1
            nodes[dst]["degree"] += 1

    shown = []
    for node in nodes.values():
        if node.pop("is_daily") and not node["degree"]:
            continue
        if orphans or node["degree"]:
            shown.append(node)
    return {"nodes": shown, "edges": edges}
