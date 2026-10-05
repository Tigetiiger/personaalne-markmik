"""app/arvuta/cells.py: cells, outputs, tables and inline values in Markdown, and writing a run back."""

from app.arvuta import cells

NOTE = """# Tellimus

## Komponendid

| Link | Kogus | Summa (€) |
| --- | --- | --- |
| https://x.ee/a | 2 |  |
| a \\| b | 3 |  |

Kokku: `= kokku ⇒ vana` ja `=  2*3 `

```python
print("ei jookse")
```

```arvuta
t = tabel("Komponendid")
```

```väljund
vana väljund
```

```arvuta-peida
x = 1
```
"""


def test_parse_finds_cells_outputs_tables_and_inline_values():
    doc = cells.parse(NOTE)
    assert [c["lang"] for c in doc["cells"]] == ["arvuta", "arvuta-peida"]
    assert doc["cells"][0]["code"] == 't = tabel("Komponendid")'
    assert doc["cells"][0]["output"] is not None and doc["cells"][1]["output"] is None
    [t] = doc["tables"]
    assert t["heading"] == "Komponendid" and t["header"] == ["Link", "Kogus", "Summa (€)"]
    assert t["rows"] == [["https://x.ee/a", "2", ""], ["a | b", "3", ""]]
    assert [x["expr"] for x in doc["inline"]] == ["kokku", "2*3"]


def test_apply_writes_outputs_tables_and_values():
    doc = cells.parse(NOTE)
    answer = {"cells": [{"out": "uus\n", "error": None}, {"out": "", "error": "NameError (rida 1): x"}],
              "tables": [{"index": 0, "header": ["Link", "Kogus", "Summa (€)"],
                          "rows": [["https://x.ee/a", "2", "4,00 €"], ["a | b", "3", "6,00 €"]]}],
              "inline": [{"value": "10,00"}, {"error": "ZeroDivisionError: x"}]}
    out = cells.apply(doc, answer)
    assert "```väljund\nuus\n```" in out and "vana väljund" not in out
    assert "```arvuta-peida\nx = 1\n```\n\n```viga\nNameError (rida 1): x\n```" in out
    assert "| a \\| b | 3 | 6,00 € |" in out
    assert "`= kokku ⇒ 10,00` ja `= 2*3 ⇒ viga: ZeroDivisionError: x`" in out
    assert 'print("ei jookse")' in out
    # A second run of the same answer changes nothing more.
    assert cells.apply(cells.parse(out), answer) == out


def test_empty_output_removes_the_block():
    doc = cells.parse(NOTE)
    out = cells.apply(doc, {"cells": [{"out": "", "error": None}, {"out": "", "error": None}]})
    assert "väljund" not in out


def test_output_fence_outgrows_backticks_in_the_output():
    assert cells.output_block("a ``` b") == ["````väljund", "a ``` b", "````"]


def test_a_note_without_cells_is_untouched():
    text = "# A\n\n| x |\n| --- |\n| 1 |\n\n`kood`\n"
    doc = cells.parse(text)
    assert cells.apply(doc, {"cells": [], "tables": [], "inline": []}) == text


def test_inputs_hash_ignores_outputs_and_table_padding():
    a = cells.inputs_hash(cells.parse(NOTE))
    b = cells.inputs_hash(cells.parse(NOTE.replace("vana väljund", "muu").replace("| 2 |", "|   2   |")))
    assert a == b
    assert a != cells.inputs_hash(cells.parse(NOTE.replace("x = 1", "x = 2")))


def test_run_up_to_one_cell_leaves_later_outputs():
    doc = cells.parse(NOTE)
    req = cells.request(doc, upto=0)
    assert req["cells"] == ['t = tabel("Komponendid")'] and req["inline"] == []
    out = cells.apply(doc, {"cells": [{"out": "esimene", "error": None}]}, upto=0)
    assert "esimene" in out and "x = 1\n```\n" in out


PLOT = "a" * 40 + ".png"
REGION_NOTE = f"""# Märkmik

| Sisend |
| --- |
| 1 |

```arvuta
df
```

```väljund
vana
```

*Tabel: df · 2 rida × 1 veergu*

| x |
| --- |
| 1 |
| 2 |

![väljund](/pildid/{PLOT})

```viga
vana viga
```

Tekst pärast.
"""


def test_an_output_region_is_one_unit_and_its_tables_are_no_inputs():
    doc = cells.parse(REGION_NOTE)
    [cell] = doc["cells"]
    start, end = cell["output"]
    assert doc["lines"][start] == "```väljund" and doc["lines"][end - 1] == "```"
    assert doc["lines"][end + 1] == "Tekst pärast."
    assert [t["header"] for t in doc["tables"]] == [["Sisend"]]


def test_a_region_is_rewritten_in_order():
    doc = cells.parse(REGION_NOTE)
    out = cells.apply(doc, {"cells": [{"out": "trükk\n", "error": "NameError: y", "images": [PLOT, "../x.png"],
                                       "tables": [{"name": "df.head()", "header": ["a", "b"],
                                                   "rows": [["1", "x | y"]], "total": [80, 2]}]}]})
    region = out.split("```arvuta\ndf\n```\n\n", 1)[1].split("\n\nTekst pärast.")[0]
    assert region == (f"```väljund\ntrükk\n```\n\n*Tabel: df.head() · 80 rida × 2 veergu (näidatud 1)*\n\n"
                      f"| a | b |\n| --- | --- |\n| 1 | x \\| y |\n\n![väljund](/pildid/{PLOT})\n\n```viga\nNameError: y\n```")
    assert cells.apply(cells.parse(out), {"cells": [{"out": "", "error": None}]}).endswith("```arvuta\ndf\n```\n\nTekst pärast.\n")


def test_a_caption_cannot_smuggle_markup():
    lines = cells.region_lines({"tables": [{"name": "*x* [a](javascript:1) <b>", "header": ["h"], "rows": [], "total": [0, 1]}]})
    assert lines[0] == "*Tabel: x a(javascript:1) b · 0 rida × 1 veergu*"
    assert cells.region_lines({"tables": [{"name": "t.sort_values('a')", "header": ["h"], "rows": [], "total": [0, 1]}]})[0] \
        == "*Tabel: t.sort_values('a') · 0 rida × 1 veergu*"


def test_a_users_own_table_caption_after_a_cell_is_not_an_output():
    text = "```arvuta\nx = 1\n```\n\n*Tabel 1: minu andmed*\n\n| a |\n| --- |\n| 1 |\n"
    doc = cells.parse(text)
    assert doc["cells"][0]["output"] is None and [t["header"] for t in doc["tables"]] == [["a"]]
    assert cells.apply(doc, {"cells": [{"out": "", "error": None}]}) == text


H1, H2 = "a" * 64, "b" * 64


def test_input_files_are_the_links_outside_output_regions():
    text = (f"Andmed: [andmed.csv](/failid/{H1}.csv) ja [andmed.csv](/failid/{H1}.csv)\n\n"
            f"```arvuta\nfail('andmed.csv')\n```\n\n📎 [tulemus.csv](/failid/{H2}.csv)\n\n[hinnad.xlsx](/failid/{H2}.xlsx)\n")
    doc = cells.parse(text)
    assert cells.input_files(doc) == [("andmed.csv", f"{H1}.csv"), ("hinnad.xlsx", f"{H2}.xlsx")]
    assert doc["cells"][0]["output"] is not None  # the 📎 line is the cell's output


def test_one_name_for_two_files_or_a_path_is_refused():
    import pytest
    with pytest.raises(ValueError, match="kahele"):
        cells.input_files(cells.parse(f"[a.csv](/failid/{H1}.csv) [a.csv](/failid/{H2}.csv)\n"))
    with pytest.raises(ValueError, match="ei sobi"):
        cells.input_files(cells.parse(f"[../a.csv](/failid/{H1}.csv)\n"))


def test_written_files_go_into_the_region():
    doc = cells.parse("```arvuta\nx\n```\n")
    out = cells.apply(doc, {"cells": [{"out": "", "error": None, "files": [
        {"label": "tulemus.csv", "name": f"{H1}.csv"}, {"label": "x](javascript:1)", "name": "../x"}]}]})
    assert out == f"```arvuta\nx\n```\n\n📎 [tulemus.csv](/failid/{H1}.csv)\n"
