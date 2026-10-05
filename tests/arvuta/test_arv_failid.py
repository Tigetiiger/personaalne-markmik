"""Data files through /api/arvuta (docs/FAILID-PLAN.md): the note's linked files reach the cell,
files it writes into valjund/ come back as 📎 links, a refused one is said in its output."""

from app import failid, notes


def stored(app, conn, data, name, note_id=None):
    with app.app_context():
        return failid.store(conn, data.encode() if isinstance(data, str) else data, name, failid.OWNER, note_id)


def test_a_cell_reads_the_notes_file_and_writes_one(app, owner, conn, local_sandbox):
    name = stored(app, conn, "kuu;kulu\njaanuar;4,5\nveebruar;5,5\n", "kulud.csv")
    body = (f"# Kulud\n\nAndmed: [kulud.csv](/failid/{name})\n\n```arvuta\ndf = fail('kulud.csv')\n"
            "df.assign(topelt=df['kulu'] * 2).to_csv('valjund/topelt.csv', index=False)\nopen('valjund/x.exe', 'w').write('x')\n"
            "print(df['kulu'].sum())\n```\n")
    n = notes.create(conn, "Kulud", body=body)
    r = owner.post("/api/arvuta", json={"slug": n["slug"], "markdown": body, "run": "all"})
    assert r.status_code == 200, r.json
    md = r.json["markdown"]
    assert "```väljund\n10.0\nvaljund/: jäi välja x.exe (seda tüüpi faile ei salvestata)\n```" in md
    [link] = [line for line in md.splitlines() if line.startswith("📎")]
    written = link.split("/failid/")[1].rstrip(")")
    assert link == f"📎 [topelt.csv](/failid/{written})"
    row = conn.execute("SELECT author, original_name FROM files WHERE name = ?", (written,)).fetchone()
    assert (row["author"], row["original_name"]) == ("arvutus", "topelt.csv")
    assert failid.allowed(conn, n["id"], written)
    # The written file is an output, not an input: a second run sends only kulud.csv.
    owner.post("/api/arvuta", json={"slug": n["slug"], "markdown": md, "run": "all"})
    assert [f["name"] for f in local_sandbox.requests[-1]["files"]] == ["kulud.csv"]


def test_a_missing_or_doubled_file_is_explained(app, owner, conn, local_sandbox):
    a = stored(app, conn, "x\n1\n", "a.csv")
    b = stored(app, conn, "x\n2\n", "b.csv")
    n = notes.create(conn, "Topelt", body="x")
    body = f"[a.csv](/failid/{a}) [a.csv](/failid/{b})\n\n```arvuta\n1\n```\n"
    r = owner.post("/api/arvuta", json={"slug": n["slug"], "markdown": body, "run": "all"})
    assert "kahele erinevale failile" in r.json["error"] and local_sandbox.requests == []
    gone = "c" * 64 + ".csv"
    r = owner.post("/api/arvuta", json={"slug": n["slug"], "markdown": f"[c.csv](/failid/{gone})\n\n```arvuta\n1\n```\n", "run": "all"})
    assert "pole enam alles" in r.json["error"]


def test_file_info(app, owner, conn):
    name = stored(app, conn, '{"a": 1}', "seaded.json")
    info = owner.get(f"/api/failid/{name}").json
    assert (info["original_name"], info["size"], info["url"]) == ("seaded.json", 8, f"/failid/{name}")
    assert owner.get("/api/failid/" + "0" * 64 + ".csv").status_code == 404
