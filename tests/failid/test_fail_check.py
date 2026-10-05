"""failid.check (what is taken in), safe_name and linked()."""

import base64
import io
import json
import zipfile

import pytest

from app import failid
from fail_helpers import CSV, xlsx

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg=="
)
PARQUET = b"PAR1" + b"\x15\x00" * 8 + b"PAR1"


@pytest.mark.parametrize("ext, data", [
    (".csv", CSV),
    (".csv", "nimi;linn\nMänd;Tõrva\n".encode("cp1257")),  # an Estonian Excel export
    (".csv", "\ufeffa,b\n1,2\n".encode()),                  # UTF-8 with a byte order mark
    (".tsv", b"a\tb\n1\t2\n"),
    (".txt", "Tere, õunapuu!\n".encode()),
    (".json", json.dumps({"a": [1, 2, "ü"]}).encode()),
    (".xlsx", xlsx()),
    (".parquet", PARQUET),
])
def test_each_type_is_accepted(ext, data):
    failid.check(data, ext)


@pytest.mark.parametrize("ext, data, reason", [
    (".exe", b"MZ", "failitüüpi"),
    (".png", PNG, "failitüüpi"),
    (".csv", PNG, "pole tekstifail"),               # a renamed binary
    (".txt", b"abc\x00def", "pole tekstifail"),
    (".csv", b"a;b\n\x81\x83\x88;1\n", "kodeering"),  # neither UTF-8 nor Windows-1257
    (".json", b"{oops", "JSON-fail on vigane"),
    (".json", b"[" * 100_000, "JSON-fail on vigane"),
    (".xlsx", PNG, "Exceli fail on vigane"),
    (".xlsx", CSV, "Exceli fail on vigane"),
    (".xlsx", xlsx({"word/document.xml": b"<document/>"}), "pole Exceli töövihik"),
    (".parquet", b"PAR1" + b"x" * 20, "pole Parquet"),
    (".parquet", b"PAR1PAR1", "pole Parquet"),
    (".parquet", CSV, "pole Parquet"),
])
def test_what_is_not_that_type_is_refused_with_a_reason(ext, data, reason):
    with pytest.raises(ValueError, match=reason):
        failid.check(data, ext)


def test_a_zip_bomb_is_refused():
    # One member unpacking to 201 MB of zeros: about 200 kB packed.
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("xl/workbook.xml", b"<workbook/>")
        with z.open("xl/worksheets/sheet1.xml", "w", force_zip64=True) as f:
            for _ in range(201):
                f.write(bytes(1024 * 1024))
    data = out.getvalue()
    assert len(data) < 1024 * 1024
    with pytest.raises(ValueError, match="lahtipakituna liiga suur"):
        failid.check(data, ".xlsx")


def test_members_together_over_the_cap_are_refused(monkeypatch):
    monkeypatch.setattr(failid, "MAX_UNPACKED", 1000)
    members = {"xl/workbook.xml": b"<workbook/>", "xl/a.xml": b"a" * 400, "xl/b.xml": b"b" * 400}
    failid.check(xlsx(members), ".xlsx")
    members["xl/c.xml"] = b"c" * 400
    with pytest.raises(ValueError, match="lahtipakituna liiga suur"):
        failid.check(xlsx(members), ".xlsx")


def test_size_cap(monkeypatch):
    monkeypatch.setattr(failid, "MAX_BYTES", len(CSV))
    failid.check(CSV, ".csv")
    with pytest.raises(ValueError, match="liiga suur"):
        failid.check(CSV + b"x", ".csv")


@pytest.mark.parametrize("original, safe", [
    ("andmed.csv", "andmed.csv"),
    ("../../etc/andmed.csv", "andmed.csv"),
    ("C:\\Users\\mari\\hinnad.XLSX", "hinnad.XLSX"),
    ("pilt\u202egnp.csv", "piltgnp.csv"),  # a right-to-left override hides the real type
    ("a" * 300 + ".parquet", "a" * 92 + ".parquet"),
    ("", ""),
])
def test_safe_name_is_a_plain_file_name(original, safe):
    assert failid.safe_name(original) == safe


def test_linked_finds_file_links_with_their_text():
    a, b = "a" * 64 + ".csv", "b" * 64 + ".xlsx"
    md = (f"Mõõtmised: [andmed.csv](/failid/{a}) · [hinnad\\_2024.xlsx](/failid/{b})\n\n"
          f"![pilt](/failid/{a})  [veeb](https://example.com/failid/{a}) [lühike](/failid/abc.csv)\n"
          f"[sulud \\[1\\].csv](/failid/{a}) [andmed.csv](/failid/{a})\n")
    assert failid.linked(md) == [("andmed.csv", a), ("hinnad_2024.xlsx", b), ("sulud [1].csv", a), ("andmed.csv", a)]
    assert failid.linked("# Pole faile\n") == []
