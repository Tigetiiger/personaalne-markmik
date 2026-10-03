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
    assert "```arvuta-peida\nx = 1\n```\n\n```väljund\nNameError (rida 1): x\n```" in out
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
