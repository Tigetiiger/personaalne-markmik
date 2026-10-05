"""Completion for Python cells (docs/TAIENDUS-PLAN.md): Jedi for names, signatures and docs, pyflakes
and the compiler for errors. The note's code is only read, never run.

A request names the cells before this one (`before`, in order) and this cell's `code`, with a
position in the cell (line from 1, col from 0). A stub in front describes the names every cell has
(tabel, mouser, euro), so `t = tabel("…")` is known to be a DataFrame.
"""

import ast

import jedi
from pyflakes import checker, messages

STUB = '''import pandas as _pd


def tabel(nimi=1) -> _pd.DataFrame:
    """Märkme tabel pandase DataFrame'ina: pealkirja nime järgi (tabel("Komponendid")) või
    järjekorranumbri järgi (tabel(1)). Muudatused kirjutatakse märkmesse tagasi."""


def mouser(lingid, kogused=None) -> _pd.DataFrame:
    """Mouseri osade andmed lingi või osa numbri järgi: veerud nr, tootja_nr, tootja, kirjeldus,
    hind (koguse järgi), laos, hoiatus."""


def euro(summa) -> str:
    """Summa eurodes nagu märkmes: euro(0.4) -> '0,40 €'."""


def fail(nimi, leht=None) -> _pd.DataFrame:
    """Märkmesse lingitud andmefail nime järgi (lingi tekst): CSV, TSV, Excel (esimene leht või
    leht=) ja Parquet DataFrame'ina, JSON andmetena, TXT tekstina. Failid on ka ploki kaustas:
    pd.read_csv("andmed.csv") töötab. Kausta valjund/ kirjutatud failid tulevad väljundisse."""
'''
MAX_ITEMS = 50
DOC_CHARS = 1500
# Only what is surely wrong before the cell runs. "Unused import" would flag every import meant
# for a later cell.
ERRORS = (messages.UndefinedName, messages.UndefinedLocal, messages.UndefinedExport,
          messages.DuplicateArgument, messages.ReturnOutsideFunction, messages.YieldOutsideFunction,
          messages.ContinueOutsideLoop, messages.BreakOutsideLoop)
WARNINGS = (messages.RedefinedWhileUnused, messages.ImportStarUsed, messages.IsLiteral,
            messages.FStringMissingPlaceholders, messages.PercentFormatInvalidFormat)


def _source(before, code, *, compiling=False):
    """(source, line offset of the cell). With compiling, earlier cells that do not parse are left
    out, so one broken cell does not hide the errors of this one."""
    parts = [STUB]
    for cell in before:
        if compiling:
            try:
                ast.parse(cell)
            except SyntaxError:
                continue
        parts.append(cell)
    head = "\n".join(parts) + "\n"
    return head + code, head.count("\n")


def complete(before, code, line, col):
    source, offset = _source(before, code)
    script = jedi.Script(source)
    found = script.complete(line + offset, col)
    prefix = _word_before(code, line, col)
    items = [c for c in found if prefix.startswith("_") or not c.name.startswith("_")][:MAX_ITEMS]
    return {"items": [{"label": c.name, "type": c.type, "detail": _detail(c)} for c in items]}


def doc(before, code, line, col, name):
    source, offset = _source(before, code)
    for c in jedi.Script(source).complete(line + offset, col):
        if c.name == name:
            return {"doc": c.docstring()[:DOC_CHARS]}
    return {"doc": ""}


def signature(before, code, line, col):
    source, offset = _source(before, code)
    sigs = jedi.Script(source).get_signatures(line + offset, col)
    if not sigs:
        return {"signature": None}
    s = sigs[0]
    return {"signature": {"label": s.to_string()[:400], "params": [p.name for p in s.params],
                          "active": s.index, "doc": s.docstring(raw=True)[:DOC_CHARS]}}


def lint(before, code):
    """Errors in this cell: the first syntax error, else pyflakes' sure ones; lines are the cell's."""
    try:
        tree = ast.parse(code)
    except SyntaxError as exc:
        line = max(1, exc.lineno or 1)
        return {"diagnostics": [{"line": line, "col": max(0, (exc.offset or 1) - 1), "end_col": None,
                                 "severity": "error", "message": f"Süntaksiviga: {exc.msg}"}]}
    del tree
    source, offset = _source(before, code, compiling=True)
    try:
        found = checker.Checker(ast.parse(source), filename="<rakk>").messages
    except SyntaxError:  # pragma: no cover - every part parsed on its own
        return {"diagnostics": []}
    out = []
    for m in sorted(found, key=lambda m: (m.lineno, m.col)):
        line = m.lineno - offset
        if line < 1 or not isinstance(m, ERRORS + WARNINGS):
            continue
        out.append({"line": line, "col": m.col, "end_col": None,
                     "severity": "error" if isinstance(m, ERRORS) else "warning",
                     "message": _estonian(m)})
    return {"diagnostics": out}


def _estonian(m):
    if isinstance(m, messages.UndefinedName):
        return f"Tundmatu nimi '{m.message_args[0]}' (pole selles ega eelmistes plokkides)"
    return m.message % m.message_args


def _detail(c):
    if c.type == "function":
        try:
            sigs = c.get_signatures()
            return sigs[0].to_string()[:120] if sigs else "funktsioon"
        except Exception:  # noqa: BLE001 - a detail is optional
            return "funktsioon"
    return {"module": "moodul", "class": "klass", "instance": "väärtus", "keyword": "võtmesõna",
            "statement": "muutuja", "param": "parameeter", "property": "omadus"}.get(c.type, c.type)


def _word_before(code, line, col):
    text = code.split("\n")[line - 1][:col] if 0 < line <= code.count("\n") + 1 else ""
    word = ""
    for ch in reversed(text):
        if ch.isalnum() or ch == "_":
            word = ch + word
        else:
            break
    return word


def handle(request):
    kind = request.get("kind")
    before = [str(c) for c in request.get("before") or []]
    code = str(request.get("code") or "")
    line, col = int(request.get("line") or 1), int(request.get("col") or 0)
    if kind == "complete":
        return complete(before, code, line, col)
    if kind == "signature":
        return signature(before, code, line, col)
    if kind == "doc":
        return doc(before, code, line, col, str(request.get("name") or ""))
    if kind == "lint":
        return lint(before, code)
    return {"error": "tundmatu päring"}
