"""One run of a note's Python cells (docs/ARVUTUS-PLAN.md), started by server.py as a fresh
process with resource limits. Reads one JSON request on stdin, writes one JSON answer as the last
line of stdout.

Request: {"cells": [code], "tables": [{"heading", "header", "rows"}], "inline": [expression],
          "mouser": {link: part | {"error"}}}
Answer:  {"cells": [{"out", "error", "tables": [{name, header, rows, total}], "images": [base64 png]}],
          "tables": [{"index", "header", "rows"}] (the note's tables a cell changed),
          "inline": [{"value"} | {"error"}], "wanted": [link]}
A cell's tables are its outputs (a DataFrame as the last value), its images the matplotlib figures
it left open (docs/JUPYTER-PLAN.md).

Nothing here is trusted: the code is the note's, and the note may have been written by a guest.
The sandbox around this process (no network, no secrets, killed afterwards) is the protection;
this file only shapes the run.
"""

import ast
import contextlib
import io
import json
import math
import re
import sys
import traceback

OUT_CAP = 20_000
TABLE_ROWS = 50
TABLE_COLUMNS = 30
IMAGES_PER_CELL = 4
IMAGE_BYTES = 2 * 1024 * 1024
# www.mouser.ee/ProductDetail/…, and with a language in the path: www.mouser.ee/en/ProductDetail/….
MOUSER_LINK = re.compile(r"^https?://(?:[a-z]{2,3}\.)?mouser\.[a-z.]{2,12}/(?:[a-z]{2}(?:-[a-z]{2,4})?/)?ProductDetail/", re.I)
MOUSER_NR = re.compile(r"^\d{2,4}-[A-Za-z0-9][A-Za-z0-9.\-/+#]*$")
# A unit is a symbol right after the number ("0,342 €", "5%") or a short word after a space
# ("12 tk"); "555P" or "595-NE555P" are part numbers, not numbers.
NUMBER = re.compile(r"^\s*(-?\d{1,3}(?:[ \u00a0\u202f]\d{3})+|-?\d+)([.,]\d+)?(?:\s*([€$%°£])|\s+([^\W\d_]{1,4}))?\s*$")
UNIT_IN_HEADER = re.compile(r"\(([^)]{1,4})\)\s*$")


def is_mouser(link):
    link = str(link or "").strip()
    return bool(MOUSER_LINK.match(link) or MOUSER_NR.match(link))


def parse_number(text):
    """'0,342 €' -> (0.342, '€'); '41 230' -> (41230, None); anything else -> None."""
    m = NUMBER.match(str(text))
    if not m:
        return None
    digits = re.sub(r"[ \u00a0\u202f]", "", m.group(1)) + (m.group(2) or "").replace(",", ".")
    value = float(digits) if m.group(2) else int(digits)
    return value, m.group(3) or m.group(4) or None


def format_number(value, unit=None):
    """Estonian: a decimal comma; a unit after a space; money (€) with at least 2 decimals."""
    if isinstance(value, bool):
        return "jah" if value else "ei"
    if isinstance(value, int):
        text = str(value)
    else:
        if math.isnan(value):
            return ""
        if value == int(value) and unit != "€" and abs(value) < 2**53:
            text = str(int(value))
        else:
            text = f"{value:.4f}".rstrip("0")
            decimals = len(text.split(".")[1])
            if unit == "€" and decimals < 2:
                text = f"{value:.2f}"
            text = text.rstrip(".").replace(".", ",")
    return f"{text} {unit}" if unit else text


def format_value(value, unit=None):
    if value is None:
        return ""
    try:
        import numpy as np
        if isinstance(value, np.generic):
            value = value.item()
    except ImportError:  # pragma: no cover - the image has numpy
        pass
    if isinstance(value, (int, float)):
        return format_number(value, unit)
    return str(value)


class Tables:
    """tabel(): the note's tables as DataFrames; the ones a cell changed are written back."""

    def __init__(self, tables):
        self.tables = tables
        self.given = {}  # table index -> DataFrame handed out (the same one every time)
        self.original = {}  # table index -> {column: the values tabel() read}

    def find(self, key):
        if isinstance(key, int):
            if not 1 <= key <= len(self.tables):
                raise KeyError(f"Märkmes pole tabelit nr {key} (tabeleid on {len(self.tables)}).")
            return key - 1
        want = str(key).strip().lstrip("#").strip().casefold()
        for i, t in enumerate(self.tables):
            if (t.get("heading") or "").casefold() == want:
                return i
        names = ", ".join(repr(t["heading"]) for t in self.tables if t.get("heading")) or "ühtegi"
        raise KeyError(f"Pealkirja {key!r} all pole tabelit. Tabelid on pealkirjade all: {names}.")

    def __call__(self, key=1):
        import pandas as pd
        i = self.find(key)
        if i in self.given:
            return self.given[i]
        t = self.tables[i]
        columns = {}
        for c, name in enumerate(t["header"]):
            cells = [row[c] if c < len(row) else "" for row in t["rows"]]
            parsed = [parse_number(x) for x in cells]
            if not any(x.strip() for x in cells):
                columns[name] = [float("nan")] * len(cells)  # empty: numbers or text may come
            elif all(p is not None or not x.strip() for p, x in zip(parsed, cells)):
                columns[name] = [p[0] if p is not None else None for p in parsed]
            else:
                columns[name] = cells
        frame = pd.DataFrame(columns, columns=t["header"])
        self.given[i] = frame
        self.original[i] = {name: [None if _missing(v) else v for v in values] for name, values in columns.items()}
        return frame

    def changed(self):
        out = []
        for i, frame in self.given.items():
            t = self.tables[i]
            header = [str(c) for c in frame.columns]
            units = {name: self.unit(t, name) for name in header}
            original = self.original[i]
            rows = []
            for r, row in enumerate(frame.itertuples(index=False, name=None)):
                cells = []
                for name, v in zip(header, row):
                    v = None if _missing(v) else v
                    # A value no cell changed keeps the text it was written with ("0,3420 €").
                    if name in original and r < len(t["rows"]) and _same(original[name][r] if r < len(original[name]) else None, v):
                        c = t["header"].index(name)
                        cells.append(t["rows"][r][c] if c < len(t["rows"][r]) else "")
                    else:
                        cells.append(format_value(v, units[name]))
                rows.append(cells)
            if header != t["header"] or rows != [list(r) + [""] * (len(t["header"]) - len(r)) for r in t["rows"]]:
                out.append({"index": i, "header": header, "rows": rows})
        return out

    @staticmethod
    def unit(table, name):
        """A column's unit: '(€)' at the end of its header, else the one its cells were written with."""
        m = UNIT_IN_HEADER.search(name)
        if m:
            return m.group(1).strip()
        if name in table["header"]:
            c = table["header"].index(name)
            for row in table["rows"]:
                parsed = parse_number(row[c]) if c < len(row) else None
                if parsed and parsed[1]:
                    return parsed[1]
        return None


def _same(before, now):
    if before is None or now is None:
        return before is None and now is None
    try:
        import numpy as np
        if isinstance(now, np.generic):
            now = now.item()
    except ImportError:  # pragma: no cover
        pass
    if isinstance(before, (int, float)) and isinstance(now, (int, float)) and not isinstance(now, bool):
        return math.isclose(before, now, rel_tol=1e-12, abs_tol=1e-12)
    return before == now


def _missing(value):
    try:
        import pandas as pd
        return value is None or (not isinstance(value, str) and bool(pd.isna(value)))
    except (TypeError, ValueError):
        return False


class Mouser:
    """mouser(lingid, kogused): part details per row from the data the app looked up. A Mouser link
    with no data yet is reported back as wanted; the app looks it up and runs again."""

    COLUMNS = ["nr", "tootja_nr", "tootja", "kirjeldus", "hind", "laos", "hoiatus"]

    def __init__(self, data):
        self.data = data
        self.wanted = []

    def __call__(self, lingid, kogused=None):
        import pandas as pd
        links = list(lingid)
        amounts = list(kogused) if kogused is not None else [1] * len(links)
        rows = [self.row(link, amount) for link, amount in zip(links, amounts)]
        index = getattr(lingid, "index", None)
        return pd.DataFrame(rows, columns=self.COLUMNS, index=index if index is not None else None)

    def row(self, link, amount):
        link = str(link or "").strip()
        empty = {c: None for c in self.COLUMNS}
        if not is_mouser(link):
            return empty
        part = self.data.get(link)
        if part is None:
            if link not in self.wanted:
                self.wanted.append(link)
            return {**empty, "hoiatus": "otsimata"}
        if part.get("error"):
            return {**empty, "hoiatus": part["error"]}
        try:
            qty = int(amount) if amount is not None and not _missing(amount) else 0
        except (TypeError, ValueError):
            qty = 0
        price = unit_price(part.get("breaks") or [], qty)
        return {"nr": part.get("nr"), "tootja_nr": part.get("tootja_nr"), "tootja": part.get("tootja"),
                "kirjeldus": part.get("kirjeldus"), "hind": price, "laos": part.get("laos"),
                "hoiatus": warning(part, qty) or None}


def unit_price(breaks, qty):
    """The price of the highest break the quantity reaches (or the first break below it)."""
    if not breaks:
        return None
    ordered = sorted((int(q), float(p)) for q, p in breaks)
    price = ordered[0][1]
    for q, p in ordered:
        if qty >= q:
            price = p
    return price


def warning(part, qty):
    notes = []
    stock, minimum, multiple = part.get("laos"), part.get("min") or 1, part.get("kordne") or 1
    if qty and stock is not None and qty > stock:
        notes.append(f"laos ainult {stock}")
    if qty and qty < minimum:
        notes.append(f"miinimum {minimum}")
    if qty and multiple > 1 and qty % multiple:
        notes.append(f"kordne {multiple}")
    return ", ".join(notes)


def euro(value):
    """A sum of money as the note writes it: euro(0.4) -> '0,40 €'."""
    return format_number(float(value), "€")


def run_cell(code, namespace, result):
    """Jupyter-like: the printed output, then the value of the last line if it is an expression; a
    DataFrame (or Series) as that value becomes a table output instead of text. Fills result's
    out and tables."""
    tree = ast.parse(code, filename="<rakk>", mode="exec")
    last, name = None, ""
    if tree.body and isinstance(tree.body[-1], ast.Expr):
        node = tree.body.pop()
        name = ast.get_source_segment(code, node.value) or ""
        last = ast.Expression(node.value)
    out = io.StringIO()
    try:
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            exec(compile(tree, "<rakk>", "exec"), namespace)
            if last is not None:
                value = eval(compile(last, "<rakk>", "eval"), namespace)
                table = as_table(value, name)
                if table is not None:
                    result["tables"].append(table)
                elif value is not None:
                    print(show(value))
    finally:
        result["out"] = out.getvalue()[:OUT_CAP]


def as_table(value, name):
    """A DataFrame or Series as {name, header, rows, total}, or None. The index is a column unless it
    is the plain 0, 1, 2, ... one."""
    try:
        import pandas as pd
    except ImportError:  # pragma: no cover
        return None
    if isinstance(value, pd.Series):
        value = value.to_frame()
    if not isinstance(value, pd.DataFrame):
        return None
    frame = value.iloc[:TABLE_ROWS, :TABLE_COLUMNS]
    plain = isinstance(frame.index, pd.RangeIndex) and frame.index.start == 0 and frame.index.step == 1
    header = ([] if plain else [str(frame.index.name or "")]) + [str(c) for c in frame.columns]
    rows = []
    for label, row in zip(frame.index, frame.itertuples(index=False, name=None)):
        cells = [format_value(None if _missing(v) else v, Tables.unit({"header": [], "rows": []}, str(c)))
                 for c, v in zip(frame.columns, row)]
        rows.append(cells if plain else [format_value(label)] + cells)
    return {"name": " ".join(name.split())[:60], "header": header, "rows": rows,
            "total": [int(value.shape[0]), int(value.shape[1])]}


def figures():
    """The matplotlib figures a cell left open, as base64 PNGs; then closed. Only when the cell used
    matplotlib (importing it costs time)."""
    if "matplotlib.pyplot" not in sys.modules:
        return []
    import base64
    plt = sys.modules["matplotlib.pyplot"]
    out = []
    for number in plt.get_fignums()[:IMAGES_PER_CELL]:
        buf = io.BytesIO()
        plt.figure(number).savefig(buf, format="png", dpi=100, bbox_inches="tight")
        if buf.tell() <= IMAGE_BYTES:
            out.append(base64.b64encode(buf.getvalue()).decode())
    plt.close("all")
    return out


def show(value):
    try:
        import pandas as pd
        if isinstance(value, (pd.DataFrame, pd.Series)):
            return value.to_string(max_rows=30, max_cols=12)
    except ImportError:  # pragma: no cover
        pass
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return format_value(value)
    return repr(value)


def short_error(exc):
    """The error as the note's author needs it: the cell's line and the message, no sandbox frames."""
    frames = [f for f in traceback.extract_tb(exc.__traceback__) if f.filename == "<rakk>"]
    where = f" (rida {frames[-1].lineno})" if frames else ""
    if isinstance(exc, SyntaxError):
        where = f" (rida {exc.lineno})" if exc.lineno else ""
        return f"SyntaxError{where}: {exc.msg}"
    message = str(exc.args[0]) if isinstance(exc, KeyError) and exc.args else str(exc)
    return f"{type(exc).__name__}{where}: {message}"


def main():
    request = json.loads(sys.stdin.readline())
    tables = Tables(request.get("tables") or [])
    mouser = Mouser(request.get("mouser") or {})
    namespace = {"__name__": "__märge__", "tabel": tables, "mouser": mouser, "euro": euro}
    cells, stopped = [], False
    for code in request.get("cells") or []:
        result = {"out": "", "error": None, "tables": [], "images": []}
        cells.append(result)
        if stopped:
            result["error"] = "Ei käivitatud: eelmises rakus oli viga."
            continue
        try:
            run_cell(code, namespace, result)
        except BaseException as exc:  # noqa: BLE001 - every failure is the note's to read
            if isinstance(exc, (SystemExit, KeyboardInterrupt)):
                exc = RuntimeError("koodi käivitus katkestati")
            result["error"] = short_error(exc)
            stopped = True
        try:
            result["images"] = figures()
        except Exception as exc:  # noqa: BLE001 - a figure that cannot be drawn
            result["error"] = result["error"] or f"Graafikut ei saanud joonistada: {exc}"
    inline = []
    for expression in request.get("inline") or []:
        try:
            inline.append({"value": format_value(eval(compile(expression, "<rakk>", "eval"), namespace))})
        except BaseException as exc:  # noqa: BLE001
            inline.append({"error": short_error(exc)})
    try:
        changed = tables.changed()
    except Exception as exc:  # noqa: BLE001 - a frame the cell broke beyond writing back
        changed = []
        cells.append({"out": "", "error": f"Tabelit ei saanud tagasi kirjutada: {exc}", "tables": [], "images": []})
    answer = {"cells": cells, "tables": changed, "inline": inline, "wanted": mouser.wanted}
    sys.__stdout__.write("\n" + json.dumps(answer, ensure_ascii=False, default=str) + "\n")
    sys.__stdout__.flush()


if __name__ == "__main__":
    main()
