"""sandbox/taiendus/engine.py: completion, signatures, docs and errors for a cell, with the cells
before it in view."""

import importlib.util
import pathlib

import pytest

spec = importlib.util.spec_from_file_location(
    "taiendus_engine", pathlib.Path(__file__).resolve().parents[2] / "sandbox" / "taiendus" / "engine.py")
engine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(engine)

BEFORE = ['t = tabel("Andmed")', "import matplotlib.pyplot as plt\nhind = 3"]


def labels(answer):
    return [i["label"] for i in answer["items"]]


def test_a_tables_methods_after_a_dot():
    out = engine.handle({"kind": "complete", "before": BEFORE, "code": "t.sort_v", "line": 1, "col": 8})
    assert labels(out) == ["sort_values"]
    assert out["items"][0]["type"] == "function" and out["items"][0]["detail"].startswith("sort_values(")


def test_names_from_earlier_cells_and_the_stub():
    out = engine.handle({"kind": "complete", "before": BEFORE, "code": "x = hi", "line": 1, "col": 6})
    assert "hind" in labels(out)
    assert "tabel" in labels(engine.handle({"kind": "complete", "before": [], "code": "ta", "line": 1, "col": 2}))
    assert not any(label.startswith("_") for label in labels(engine.handle(
        {"kind": "complete", "before": [], "code": "t = tabel()\nt.", "line": 2, "col": 2})))


def test_signature_and_doc():
    out = engine.handle({"kind": "signature", "before": BEFORE, "code": "t.sort_values(", "line": 1, "col": 14})
    assert out["signature"]["label"].startswith("sort_values(") and out["signature"]["active"] == 0
    assert "by" in out["signature"]["params"]
    doc = engine.handle({"kind": "doc", "before": [], "code": "euro", "line": 1, "col": 4, "name": "euro"})
    assert "0,40 €" in doc["doc"]


def test_lint_marks_unknown_names_in_this_cell_only():
    out = engine.handle({"kind": "lint", "before": ["a = 1", "def f(:"], "code": "print(a)\nprint(b)\nimport os"})
    assert out["diagnostics"] == [{"line": 2, "col": 6, "end_col": None, "severity": "error",
                                   "message": "Tundmatu nimi 'b' (pole selles ega eelmistes plokkides)"}]


def test_lint_reports_a_syntax_error_at_its_line():
    [d] = engine.handle({"kind": "lint", "before": [], "code": "x = 1\nif x\n    pass"})["diagnostics"]
    assert d["line"] == 2 and d["severity"] == "error" and d["message"].startswith("Süntaksiviga")


def test_names_of_later_cells_are_unknown():
    out = engine.handle({"kind": "lint", "before": [], "code": "hind * 2"})
    assert [d["message"] for d in out["diagnostics"]] == ["Tundmatu nimi 'hind' (pole selles ega eelmistes plokkides)"]


@pytest.mark.parametrize("kind", ["midagi", None])
def test_unknown_kind(kind):
    assert engine.handle({"kind": kind}) == {"error": "tundmatu päring"}


@pytest.fixture
def local_engine(monkeypatch):
    from app.arvuta import taiendus
    monkeypatch.setattr(taiendus, "send", engine.handle)


def test_the_endpoint(owner, local_engine):
    r = owner.post("/api/arvuta/taienda", json={"kind": "complete", "before": BEFORE, "code": "t.sort_v", "line": 1, "col": 8})
    assert r.status_code == 200 and labels(r.json) == ["sort_values"]
    assert owner.post("/api/arvuta/taienda", json={"kind": "run", "code": ""}).status_code == 400
    assert owner.post("/api/arvuta/taienda", json={"kind": "lint", "code": 5}).status_code == 400
    assert owner.post("/api/arvuta/taienda", json={"kind": "lint", "code": "x" * 200_001}).status_code == 413
    r = owner.post("/api/arvuta/taienda", json={"kind": "doc", "code": "euro", "line": 1, "col": 4, "name": "euro"})
    assert "€" in r.json["doc"]


def test_the_endpoint_is_the_owners(client):
    assert client.post("/api/arvuta/taienda", json={"kind": "lint", "code": ""}).status_code in (302, 401, 403)


def test_without_the_container(owner, monkeypatch):
    monkeypatch.setenv("TAIENDUS_SOCKET", "/nonexistent/taiendus.sock")
    r = owner.post("/api/arvuta/taienda", json={"kind": "lint", "code": "x"})
    assert r.json["error"].startswith("Täiendus pole kättesaadav")


@pytest.mark.docker
def test_the_real_container():
    import os

    from app.arvuta import taiendus
    if not os.path.exists(os.environ.get("TAIENDUS_SOCKET", "/run/taiendus/taiendus.sock")):
        pytest.skip("no taiendus socket")
    out = taiendus.send({"kind": "complete", "before": BEFORE, "code": "t.sort_v", "line": 1, "col": 8})
    assert labels(out) == ["sort_values"]
    probe = taiendus.send({"kind": "complete", "before": [], "code": "import socket\nsocket.create_connection(('1.1.1.1', 80))\nsock",
                           "line": 3, "col": 4})
    assert "socket" in labels(probe)  # read, never run: nothing else happens
