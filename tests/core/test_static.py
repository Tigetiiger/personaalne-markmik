"""/static: the bundle's built files (tools/build-app.mjs) and how they are cached."""

import json

import pytest

from app.views import core

GZIP = {"Accept-Encoding": "gzip"}


@pytest.fixture
def dist(tmp_path, monkeypatch):
    """A small built bundle: an entry with a .gz copy, a chunk without one, the manifest and a
    file the build did not write."""
    (tmp_path / "chunks").mkdir()
    (tmp_path / "app-AAAA1111.js").write_text("export const a = 1;\n")
    (tmp_path / "app-AAAA1111.js.gz").write_bytes(__import__("gzip").compress(b"export const a = 1;\n"))
    (tmp_path / "chunks" / "chunk-BBBB2222.js").write_text("export const b = 2;\n")
    (tmp_path / "stray.js").write_text("alert(1);\n")
    (tmp_path / "manifest.json").write_text(json.dumps({
        "files": {"app.js": "app-AAAA1111.js"},
        "modules": ["app.js"],
        "preload": {"*": ["app-AAAA1111.js", "chunks/chunk-BBBB2222.js"]},
        "lazy": [],
        "outputs": ["app-AAAA1111.js", "chunks/chunk-BBBB2222.js"],
    }))
    monkeypatch.setattr(core, "DIST_DIR", tmp_path)
    return tmp_path


def test_only_the_builds_hashed_files_are_cached_for_good(client, dist):
    for name in ("app-AAAA1111.js", "chunks/chunk-BBBB2222.js"):
        r = client.get(f"/static/dist/{name}")
        assert r.status_code == 200, name
        assert r.headers["Cache-Control"] == core.IMMUTABLE
    for name in ("manifest.json", "stray.js", "app-AAAA1111.js.gz", "..%2Fapp-AAAA1111.js", "nothing.js"):
        assert client.get(f"/static/dist/{name}", headers=GZIP).status_code == 404, name


def test_a_file_with_a_gz_copy_varies_by_encoding_either_way(client, dist):
    zipped = client.get("/static/dist/app-AAAA1111.js", headers=GZIP)
    assert zipped.headers["Content-Encoding"] == "gzip"
    assert zipped.mimetype == "text/javascript"
    plain = client.get("/static/dist/app-AAAA1111.js", headers={"Accept-Encoding": "identity"})
    assert "Content-Encoding" not in plain.headers
    assert plain.get_data(as_text=True) == "export const a = 1;\n"
    for r in (zipped, plain):
        assert "Accept-Encoding" in r.headers["Vary"]
    assert "Accept-Encoding" not in client.get("/static/dist/chunks/chunk-BBBB2222.js").headers.get("Vary", "")


def test_static_files_do_not_vary_by_the_session_cookie(owner, dist):
    # The owner's session is read on every request; a Vary: Cookie made Firefox ask again for
    # every cached file, because the cookie is re-signed on every response.
    for url in ("/static/dist/app-AAAA1111.js", "/static/app.js", "/static/style.css"):
        r = owner.get(url, headers=GZIP)
        assert r.status_code == 200, url
        assert "Cookie" not in r.headers.get("Vary", ""), url
        assert "Set-Cookie" not in r.headers, url


def test_a_bundled_module_answers_with_its_built_copy(client, dist):
    r = client.get("/static/app.js")
    assert r.get_data(as_text=True) == 'export * from "/static/dist/app-AAAA1111.js";\n'
    assert r.headers["Cache-Control"] == "no-cache"
