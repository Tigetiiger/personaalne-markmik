"""app/linkfmt.py: note body <-> brain file. The round trip must give back exactly what the editor
wrote; the file form must be what gbrain turns into edges (docs/spikes/gbrain.md)."""

import json

import pytest

from app import linkfmt

def row(**kw):
    base = {"id": 7, "title": "Matemaatika", "tags_json": "[]", "aliases_json": "[]",
            "frontmatter_extra": "{}", "created_at": "2026-09-23T22:30:00Z",
            "updated_at": "2026-09-24T08:00:00Z"}
    return {**base, **kw}


# (editor text, the same text in the file)
CASES = [
    ("[Matemaatika](/m/matemaatika)", "[[matemaatika|Matemaatika]]"),
    ("[matemaatika](/m/matemaatika)", "[[matemaatika]]"),
    ("[a | b](/m/a)", "[[a|a | b]]"),
    ("[a \\] b](/m/a)", "[a \\] b](/m/a)"),                   # ] cannot live in a wikilink alias
    ("[a [b] c](/m/a)", "[a [b] c](/m/a)"),
    ("[a \\[ b](/m/a)", "[[a|a \\[ b]]"),
    ("[](/m/a)", "[](/m/a)"),
    ("[**Füüsika** ja `x`](/m/fyysika)", "[[fyysika|**Füüsika** ja `x`]]"),
    ("[Pealkiri](/m/keemia#osa-2)", "[[keemia#osa-2|Pealkiri]]"),
    ('[T](/m/keemia "vihje")', '[T](/m/keemia "vihje")'),
    ("[T]( /m/keemia )", "[T]( /m/keemia )"),
    ("![pilt](/m/keemia)", "![pilt](/m/keemia)"),
    ("\\[x](/m/a)", "\\[x](/m/a)"),
    ("[[a](/m/a)", "[[[a]]"),
    ("[a](/m/a)]", "[[a]]]"),
    ("`[a](/m/a)` ja [a](/m/a)", "`[a](/m/a)` ja [[a]]"),
    ("```\n[a](/m/a)\n![p](/pildid/x.png)\n```\n[a](/m/a)\n", "```\n[a](/m/a)\n![p](/pildid/x.png)\n```\n[[a]]\n"),
    ("~~~md\n[a](/m/a)\n~~~", "~~~md\n[a](/m/a)\n~~~"),
    ("- [ ] tee [Keemia](/m/keemia) @reede\n  - [x] [a](/m/a)\n", "- [ ] tee [[keemia|Keemia]] @reede\n  - [x] [[a]]\n"),
    ("| aine | link |\n| --- | --- |\n| x | [a \\| b](/m/a) |\n", "| aine | link |\n| --- | --- |\n| x | [[a|a \\| b]] |\n"),
    ("> [Keemia](/m/keemia)", "> [[keemia|Keemia]]"),
    ("![pilt](/pildid/ab12.png) <img src=\"/pildid/cd.jpg\">", "![pilt](/notes/_pildid/ab12.png) <img src=\"/notes/_pildid/cd.jpg\">"),
    ("https://x.ee/pildid/a.png", "https://x.ee/pildid/a.png"),
    ("[[pages/foo|Foo]] ja [[Suur Algustäht]]", "[[pages/foo|Foo]] ja [[Suur Algustäht]]"),
    # a link to a note that does not exist (yet, or any more) is a wikilink like any other;
    # typed slug-shaped wikilinks stay text
    ("vt [Kustutatud](/m/kustutatud)", "vt [[kustutatud|Kustutatud]]"),
    ("[[design-preferences]]", "\\[[design-preferences]]"),
    ("[[keemia]] ja [[a|A]] kirjutatud", "\\[[keemia]] ja \\[[a|A]] kirjutatud"),
    ("\\[[keemia]] ja \\\\[[a]]", "\\\\[[keemia]] ja \\\\\\[[a]]"),
    ("[[notes/kool/x|X]] ![[keemia]]", "\\[[notes/kool/x|X]] ![[keemia]]"),
    ("[a](/m/a)\n\n    [[a|b]] taandega kood", "[[a]]\n\n    \\[[a|b]] taandega kood"),
    ("[Päevik](/m/2026-09-24)", "[[2026-09-24|Päevik]]"),
    ("# Pealkiri\n\nilma reavahetuseta lõpus", "# Pealkiri\n\nilma reavahetuseta lõpus"),
    ("\n\nalgab tühja reaga\n", "\n\nalgab tühja reaga\n"),
    ("---\nnagu frontmatter\n---\n", "---\nnagu frontmatter\n---\n"),
    ("", ""),
]


@pytest.mark.parametrize("body, in_file", CASES)
def test_round_trip(body, in_file):
    text = linkfmt.to_file(row(), body)
    assert text.endswith("---\n\n" + in_file)
    meta, back = linkfmt.from_file(text)
    assert back == body
    assert meta["id"] == 7


def test_basename_taken_uses_the_path_form():
    body = "[Märkmik](/m/markmik) ja [markmik](/m/markmik) ja [K](/m/keemia)"
    text = linkfmt.to_file(row(), body, basename_taken={"markmik": "kool/projektid"})
    assert "[[notes/kool/projektid/markmik|Märkmik]] ja [[notes/kool/projektid/markmik]] ja [[keemia|K]]" in text
    assert linkfmt.from_file(text)[1] == body
    root = linkfmt.to_file(row(), "[x](/m/markmik)", basename_taken={"markmik": ""})
    assert root.endswith("[[notes/markmik|x]]")


def test_every_slug_shaped_wikilink_is_an_app_link():
    """Whether the note exists does not matter: a link to a deleted (or not yet created) note
    keeps being a link, and comes back unchanged."""
    laptop = "[[notes/kool/uus|Uus]] [[uus|Uus]] [[tulevane]] [[pages/x]] [[Suur]] \\[[notes/y]]"
    _, body = linkfmt.from_file(laptop)
    assert body == "[Uus](/m/uus) [Uus](/m/uus) [tulevane](/m/tulevane) [[pages/x]] [[Suur]] [[notes/y]]"
    assert linkfmt.to_file(row(), body).endswith(
        "[[uus|Uus]] [[uus|Uus]] [[tulevane]] [[pages/x]] [[Suur]] \\[[notes/y]]")


def test_frontmatter():
    note = row(title="Töö: plaan #1", tags_json=json.dumps(["kool", "töö"]), aliases_json=json.dumps(["Plaan"]),
               frontmatter_extra=json.dumps({"status": "draft", "kuupäev": "2026-01-02", "n": [1, 2]}))
    text = linkfmt.to_file(note, "# Töö: plaan #1\n")
    assert text == (
        "---\n"
        "title: 'Töö: plaan #1'\n"
        "type: note\n"
        "tags: [kool, töö]\n"
        "aliases: [Plaan]\n"
        "date: 2026-09-24\n"          # created 22:30 UTC = the next day in Tallinn
        "updated: 2026-09-24\n"
        "source: markmik\n"
        "id: 7\n"
        "status: draft\n"
        "kuupäev: 2026-01-02\n"
        "n: [1, 2]\n"
        "---\n\n# Töö: plaan #1\n")
    meta, _ = linkfmt.from_file(text)
    assert meta["kuupäev"] == "2026-01-02" and meta["date"] == "2026-09-24" and meta["n"] == [1, 2]
    assert meta["tags"] == ["kool", "töö"] and meta["title"] == "Töö: plaan #1"


def test_no_aliases_key_when_empty():
    assert "aliases" not in linkfmt.to_file(row(), "x")


@pytest.mark.parametrize("text, meta, body", [
    ("---\nfoo: !!set {a: null}\nt: !!timestamp 2026-01-01\nb: !!binary aGVsbG8=\n1: yks\n---\nkeha",
     {"foo": ["a"], "t": "2026-01-01", "b": "aGVsbG8=", "1": "yks"}, "keha"),  # JSON values only
    ("---\ns: !!set {b: null, a: null, 3: null}\n---\nkeha", {"s": [3, "a", "b"]}, "keha"),
    ("---\n? [a, b]\n: c\n---\nkeha", {}, "---\n? [a, b]\n: c\n---\nkeha"),
    ("# Ilma frontmatterita\n\n[[keemia]]\n", {}, "# Ilma frontmatterita\n\n[keemia](/m/keemia)\n"),
    ("---\r\ntitle: X\r\nfoo: bar\r\n---\r\n\r\n# X\r\nrida\r\n", {"title": "X", "foo": "bar"}, "# X\nrida\n"),
    ("---\ntitle: X\n---\n# X", {"title": "X"}, "# X"),
    ("---\n---\n\nkeha", {}, "keha"),
    ("---\nlihtsalt tekst\n---\nkeha", {}, "---\nlihtsalt tekst\n---\nkeha"),
    ("---\n: [vigane\n---\nkeha", {}, "---\n: [vigane\n---\nkeha"),
    ("---\ntitle: X\n", {}, "---\ntitle: X\n"),
])
def test_files_written_on_the_laptop(text, meta, body):
    assert linkfmt.from_file(text) == (meta, body)


def test_laptop_file_is_stable_after_one_import():
    laptop = "---\ntitle: X\ndate: 2026-01-02\nextra: [a, b]\n---\n# X\n[[keemia]] ja [[pages/foo]]\n"
    meta, body = linkfmt.from_file(laptop)
    extra = {k: v for k, v in meta.items() if k not in linkfmt.KNOWN_KEYS}
    text = linkfmt.to_file(row(title="X", frontmatter_extra=json.dumps(extra)), body)
    assert "extra: [a, b]\n" in text and text.endswith("# X\n[[keemia]] ja [[pages/foo]]\n")
    assert linkfmt.from_file(text)[1] == body


def test_image_names_skip_code():
    body = "![a](/pildid/a1.png) `![b](/pildid/b.png)` <img src=\"/pildid/c.webp\">\n```\n/pildid/d.png\n```\n"
    assert linkfmt.image_names(body) == {"a1.png", "c.webp"}


def test_round_trip_of_mixed_fragments():
    import random
    rng = random.Random(4)
    pieces = [b for b, _ in CASES] + ["[", "]", "`", "|", "\\", "!", "\n", "```\n", " ", "[[", "]]", "(/m/a)",
                                      "a", "keemia", "notes/", "#h", "gone"]
    for _ in range(1500):
        body = "".join(rng.choice(pieces) for _ in range(rng.randint(1, 8)))
        text = linkfmt.to_file(row(), body, basename_taken={"keemia": "kool"})
        assert linkfmt.from_file(text)[1] == body, body
