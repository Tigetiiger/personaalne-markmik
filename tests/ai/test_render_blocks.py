"""POST /api/render/blocks: the AI review window's Markdown blocks as sanitised HTML."""

from app import notes
from app.views import ai


def render(owner, blocks):
    return owner.post("/api/render/blocks", json={"blocks": blocks})


def test_blocks_render_like_the_rest_of_the_app(owner):
    r = render(owner, ["# Pealkiri", "- [x] tehtud", "Tekst **rasvane** vanauus."])
    assert r.status_code == 200
    assert r.get_json()["html"] == [
        "<h1>Pealkiri</h1>",
        '<ul class="task-list">\n<li class="task-list-item"><input type="checkbox" checked="" disabled="disabled"> tehtud</li>\n</ul>',
        # The review's markers go through untouched.
        "<p>Tekst <strong>rasvane</strong> vanauus.</p>",
    ]


def test_html_in_a_block_is_sanitised(owner):
    html = render(owner, [
        "<script>alert(1)</script>",
        '<img src="x" onerror="alert(1)">',
        "[klõpsa](javascript:alert(1))",
        '<a href="/m/x" onclick="alert(1)" style="color:red">x</a>',
    ]).get_json()["html"]
    joined = "".join(html).lower()
    assert "<script" not in joined and "onerror" not in joined and "javascript:" not in joined
    assert "onclick" not in joined and "style=" not in joined
    assert '<img src="x">' in html[1]


def test_bad_and_oversized_requests(owner, monkeypatch):
    assert render(owner, "nope").status_code == 400
    assert render(owner, ["a", 1]).status_code == 400
    assert owner.post("/api/render/blocks", data="x").status_code == 400
    monkeypatch.setattr(ai, "RENDER_CAP", 10)
    r = render(owner, ["12345", "678901"])
    assert r.status_code == 413 and r.get_json() == {"error": "Muudatus on eelvaateks liiga suur."}
    monkeypatch.setattr(ai, "RENDER_BLOCKS", 1)
    assert render(owner, ["a", "b"]).status_code == 413


def test_owner_only(client, conn):
    assert client.post("/api/render/blocks", json={"blocks": ["x"]}).status_code == 401
    note = notes.create(conn, "Jagatud")
    conn.execute("INSERT INTO shares (token_hash, note_id, mode) VALUES ('h', ?, 'edit')", (note["id"],))
    with client.c.session_transaction() as s:
        s["share_id"] = conn.execute("SELECT id FROM shares").fetchone()["id"]
    assert client.post("/api/render/blocks", json={"blocks": ["x"]}).status_code == 401
