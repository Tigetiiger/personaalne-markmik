"""sandbox/kernel.py: one run of a note's cells, as the sandbox server starts it."""

import pytest

from arv_kernel import run_kernel

TABLE = {"heading": "Komponendid", "header": ["Link", "Kogus", "Ühikuhind (€)", "Summa (€)"],
         "rows": [["https://x.ee/a", "25", "0,342 €", ""], ["", "4", "1,5", ""]]}


def run(cells, tables=(), inline=(), mouser=None):
    return run_kernel({"cells": list(cells), "tables": list(tables), "inline": list(inline), "mouser": mouser or {}})


def test_cells_share_variables_and_show_the_last_value_like_jupyter():
    out = run(["x = 2\nprint('tere')", "x * 21", "y = x"])
    assert [c["out"] for c in out["cells"]] == ["tere\n", "42\n", ""]
    assert all(c["error"] is None for c in out["cells"])


def test_an_error_names_the_line_and_stops_later_cells():
    out = run(["a = 1\nb = a / 0", "print('ei')"])
    assert out["cells"][0]["error"] == "ZeroDivisionError (rida 2): division by zero"
    assert out["cells"][1]["error"].startswith("Ei käivitatud")
    assert run(["def f(:"])["cells"][0]["error"].startswith("SyntaxError (rida 1)")


def test_table_columns_are_numbers_and_changes_are_written_back():
    out = run(["t = tabel('Komponendid')\nt['Summa (€)'] = t['Kogus'] * t['Ühikuhind (€)']\nkokku = t['Summa (€)'].sum()"],
              [TABLE], ["kokku", "puudub"])
    [changed] = out["tables"]
    assert changed["index"] == 0 and changed["header"] == TABLE["header"]
    assert changed["rows"] == [["https://x.ee/a", "25", "0,342 €", "8,55 €"], ["", "4", "1,5", "6,00 €"]]
    assert out["inline"][0] == {"value": "14,55"}
    assert out["inline"][1]["error"].startswith("NameError")


def test_an_unchanged_table_is_not_written_back():
    out = run(["t = tabel(1)\nlen(t)"], [{"heading": None, "header": ["A", "B"], "rows": [["1", "x"], ["2,5", ""]]}])
    assert out["tables"] == [] and out["cells"][0]["out"] == "2\n"


def test_a_missing_table_says_which_ones_exist():
    out = run(["tabel('Puudub')"], [TABLE])
    assert "Komponendid" in out["cells"][0]["error"]


def test_mouser_rows_price_by_quantity_and_warnings():
    link = "https://www.mouser.ee/ProductDetail/Texas-Instruments/NE555P?qs=abc"
    part = {"nr": "595-NE555P", "tootja_nr": "NE555P", "tootja": "TI", "kirjeldus": "Timer",
            "breaks": [[1, 0.44], [10, 0.38], [100, 0.3]], "laos": 50, "min": 5, "kordne": 5}
    table = {"heading": None, "header": ["Link", "Kogus"],
             "rows": [[link, "25"], [link, "3"], [link, "60"], ["https://lcsc.com/x", "1"], ["595-UUS", "1"]]}
    out = run(["t = tabel(1)\nm = mouser(t['Link'], t['Kogus'])\nt['Hind'] = m['hind']\nt['Hoiatus'] = m['hoiatus']"],
              [table], mouser={link: part})
    rows = out["tables"][0]["rows"]
    assert [r[2] for r in rows] == ["0,38", "0,44", "0,38", "", ""]
    assert [r[3] for r in rows] == ["", "miinimum 5, kordne 5", "laos ainult 50", "", "otsimata"]
    assert out["wanted"] == ["595-UUS"]


def test_mouser_error_is_the_warning():
    out = run(["t = tabel(1)\nt['H'] = mouser(t['Link'])['hoiatus']"],
              [{"heading": None, "header": ["Link"], "rows": [["595-X"]]}], mouser={"595-X": {"error": "ei leitud"}})
    assert out["tables"][0]["rows"] == [["595-X", "ei leitud"]]


@pytest.mark.parametrize("text, value", [
    ("0,342 €", (0.342, "€")), ("41 230", (41230, None)), ("41 230", (41230, None)), ("-2.5", (-2.5, None)),
    ("2026-10-03", None), ("595-NE555P", None), ("abc", None), ("12 tk", (12, "tk")), ("555P", None),
    ("595-X", None), ("5%", (5, "%")), ("1 234,50 €", (1234.5, "€")), ("12", (12, None)),
])
def test_parse_number(text, value):
    import importlib.util
    import pathlib
    spec = importlib.util.spec_from_file_location("kernel", pathlib.Path(__file__).resolve().parents[2] / "sandbox" / "kernel.py")
    kernel = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kernel)
    assert kernel.parse_number(text) == value


def test_cells_nobody_changed_keep_their_text():
    table = {"heading": None, "header": ["A (€)", "B"], "rows": [["0,3420 €", "1"], ["2", "2"]]}
    out = run(["t = tabel(1)\nt['B'] = t['B'] * 10"], [table])
    assert out["tables"][0]["rows"] == [["0,3420 €", "10"], ["2", "20"]]


def test_the_mouser_template_computes_its_table():
    import pathlib

    from app.arvuta import cells
    text = (pathlib.Path(__file__).resolve().parents[2] / "app" / "arvuta" / "mouser_mall.md").read_text()
    link = "https://www.mouser.ee/ProductDetail/Texas-Instruments/NE555P?qs=abc"
    text = text.replace("|  | 1 |  |  |  |  |  |  |  |  |",
                        f"| {link} | 25 |  |  |  |  |  |  |  |  |\n| https://lcsc.com/x | 4 |  |  |  | Takisti | 0,10 € |  |  |  |\n|  | 1 |  |  |  |  |  |  |  |  |")
    doc = cells.parse(text)
    part = {"nr": "595-NE555P", "tootja_nr": "NE555P", "tootja": "Texas Instruments", "kirjeldus": "Timer",
            "breaks": [[1, 0.44], [10, 0.342]], "laos": 41230, "min": 1, "kordne": 1}
    out = run_kernel({**cells.request(doc), "mouser": {link: part}})
    assert [c["error"] for c in out["cells"]] == [None]
    new = cells.apply(doc, out)
    assert f"| {link} | 25 | 595-NE555P | NE555P | Texas Instruments | Timer | 0,342 € | 8,55 € | 41230 |  |" in new
    assert "| https://lcsc.com/x | 4 |  |  |  | Takisti | 0,10 € | 0,40 € |  |  |" in new
    assert "|  | 1 |  |  |  |  |  |  |  |  |" in new
    assert "`= euro(kokku) ⇒ 8,95 €`" in new
    # The last row is empty: no row is added.
    assert new.count("|  | 1 |") == 1
    # Run again on the result: nothing changes.
    again = cells.parse(new)
    assert cells.apply(again, run_kernel({**cells.request(again), "mouser": {link: part}})) == new


def test_mall_command_makes_the_template_once(conn):
    from app import mallid
    from app.arvuta import mall
    note, made = mall.make(conn)
    assert made and mallid.is_template(note) and mallid.defaults(note)["folder"] == "tellimused"
    assert mall.make(conn)[1] is False


def test_euro():
    assert [c["out"] for c in run(["euro(0.4)", "euro(1234.5)"])["cells"]] == ["'0,40 €'\n", "'1234,50 €'\n"]


def test_a_link_with_a_language_in_its_path_is_a_mouser_link():
    en = "https://www.mouser.ee/en/ProductDetail/859-LTST-C190KRKT"
    out = run(["t = tabel(1)\nt['H'] = mouser(t['Link'])['hoiatus']"],
              [{"heading": None, "header": ["Link"], "rows": [[en]]}])
    assert out["wanted"] == [en] and out["tables"][0]["rows"] == [[en, "otsimata"]]


def test_the_mouser_template_adds_an_empty_row_after_a_filled_last_one():
    import pathlib

    from app.arvuta import cells
    text = (pathlib.Path(__file__).resolve().parents[2] / "app" / "arvuta" / "mouser_mall.md").read_text()
    link = "https://www.mouser.ee/en/ProductDetail/859-LTST-C190KRKT"
    text = text.replace("|  | 1 |  |  |  |  |  |  |  |  |", f"| {link} | 10 |  |  |  |  |  |  |  |  |")
    part = {"nr": "859-LTST-C190KRKT", "tootja_nr": "LTST-C190KRKT", "tootja": "LITEON", "kirjeldus": "LED",
            "breaks": [[1, 0.17], [10, 0.084]], "laos": 448117, "min": 1, "kordne": 1}
    doc = cells.parse(text)
    new = cells.apply(doc, run_kernel({**cells.request(doc), "mouser": {link: part}}))
    rows = [line for line in new.splitlines() if line.startswith("| ") and "---" not in line][1:]
    assert rows == [f"| {link} | 10 | 859-LTST-C190KRKT | LTST-C190KRKT | LITEON | LED | 0,084 € | 0,84 € | 448117 |  |",
                    "|  | 1 |  |  |  |  |  |  |  |  |"]
    assert "`= euro(kokku) ⇒ 0,84 €`" in new
    # A second run keeps one empty row: nothing more changes.
    again = cells.parse(new)
    assert cells.apply(again, run_kernel({**cells.request(again), "mouser": {link: part}})) == new


def test_a_dataframe_as_the_last_value_is_a_table_output():
    out = run(["import pandas as pd\ndf = pd.DataFrame({'a': [1, 2], 'hind (€)': [0.5, 1.25]})\ndf",
               "s = pd.Series([3, 4], index=['x', 'y'], name='n')\ns",
               "big = pd.DataFrame({'i': range(80)})\nbig"])
    first, second, third = out["cells"]
    assert first["out"] == "" and first["tables"] == [
        {"name": "df", "header": ["a", "hind (€)"], "rows": [["1", "0,50 €"], ["2", "1,25 €"]], "total": [2, 2]}]
    assert second["tables"][0]["header"] == ["", "n"] and second["tables"][0]["rows"] == [["x", "3"], ["y", "4"]]
    assert len(third["tables"][0]["rows"]) == 50 and third["tables"][0]["total"] == [80, 1]


def test_a_plot_is_a_png_and_figures_are_closed():
    import base64
    out = run(["import matplotlib\nmatplotlib.use('Agg')\nimport matplotlib.pyplot as plt\nplt.plot([1, 2, 3])\nplt.figure()\nplt.bar([1], [2])",
               "print(len(plt.get_fignums()))"])
    first, second = out["cells"]
    assert len(first["images"]) == 2 and base64.b64decode(first["images"][0])[:8] == b"\x89PNG\r\n\x1a\n"
    assert second["out"] == "0\n" and second["images"] == []
    assert first["out"] == ""  # plt.bar's return value is not printed


def test_an_error_keeps_the_output_printed_before_it():
    [cell] = run(["print('enne')\n1 / 0"])["cells"]
    assert cell["out"] == "enne\n" and cell["error"].startswith("ZeroDivisionError")


# --- Data files (docs/FAILID-PLAN.md) -------------------------------------------------------------

def b64(data):
    import base64
    return base64.b64encode(data if isinstance(data, bytes) else data.encode()).decode()


def run_files(cells, files):
    return run_kernel({"cells": list(cells), "tables": [], "inline": [], "mouser": {},
                       "files": [{"name": n, "data": b64(d)} for n, d in files.items()]})


def test_fail_reads_each_type():
    import io as _io
    import pandas as pd
    xlsx, parquet = _io.BytesIO(), _io.BytesIO()
    pd.DataFrame({"a": [1, 2]}).to_excel(xlsx, index=False)
    pd.DataFrame({"p": [7]}).to_parquet(parquet)
    files = {"koma.csv": "x,y\n1,2.5\n", "semikoolon.csv": "x;y\n1;2,5\n", "tab.tsv": "x\ty\n1\t2\n",
             "andmed.json": '{"k": [1, 2]}', "märkus.txt": "tere", "tabel.xlsx": xlsx.getvalue(),
             "p.parquet": parquet.getvalue(), "vana.csv": "nimi;arv\nõun;1\n".encode("cp1257")}
    out = run_files(["print(fail('koma.csv')['y'].sum(), fail('semikoolon.csv')['y'].sum(), list(fail('tab.tsv').columns))",
                     "print(fail('andmed.json')['k'], fail('märkus.txt'), fail('tabel.xlsx')['a'].sum(), fail('p.parquet')['p'][0])",
                     "print(fail('vana.csv')['nimi'][0])",
                     "import pandas as pd\nprint(len(pd.read_csv('koma.csv')))"], files)
    assert [c["out"] for c in out["cells"]] == ["2.5 2.5 ['x', 'y']\n", "[1, 2] tere 3 7\n", "õun\n", "1\n"]


def test_a_missing_file_names_the_ones_there():
    out = run_files(["fail('puudub.csv')"], {"a.csv": "x\n1\n"})
    assert out["cells"][0]["error"] == "FileNotFoundError (rida 1): Märkmes pole faili 'puudub.csv'. Märkme failid: a.csv."


def test_files_a_cell_writes_into_valjund_come_back_with_it():
    import base64
    out = run_files(["import pandas as pd\npd.DataFrame({'a': [1]}).to_csv('valjund/tulemus.csv', index=False)\nopen('valjund/pilt.png', 'wb').write(b'x')",
                     "print('teine')"], {})
    first, second = out["cells"]
    assert [f["name"] for f in first["files"]] == ["tulemus.csv"]
    assert base64.b64decode(first["files"][0]["data"]) == b"a\n1\n"
    assert "pilt.png (seda tüüpi faile ei salvestata)" in first["out"]
    assert second["files"] == []  # unchanged files are not sent again


def test_bad_file_names_are_refused():
    for name in ("../x.csv", "a/b.csv", "valjund", ".."):
        assert run_files(["1"], {name: "x"}) == {"error": "Vigane failinimi."}
