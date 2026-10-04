"""The computing parts of a note's Markdown: cells (```arvuta and ```arvuta-peida fences), their
output regions, pipe tables with the heading above them, and inline values (`= expression ⇒
value`). parse() finds them, apply() writes a sandbox answer back.

A cell's output region (docs/JUPYTER-PLAN.md) is what follows it, made only of these blocks: a
```väljund fence (printed text), a ```viga fence (the error), "*Tabel: …*" with the table under it
(a DataFrame), and ![väljund](/pildid/<sha>.png) (a plot). The app rewrites the region on every
run; tables in it are outputs, never the note's tables.

Muya keeps only the first word of a fence's info string, so the cell kinds are languages of their
own; a plain ```python block is code to read and never runs.
"""

import hashlib
import json
import re

CELL_LANGS = ("arvuta", "arvuta-peida")
OUTPUT_LANG = "väljund"
ERROR_LANG = "viga"
# Only the caption the app writes (region_lines): a user's own "*Tabel 1: …*" stays theirs.
CAPTION = re.compile(r"^\*Tabel(?:: [^*\n]*)? · \d+ rida × \d+ veergu(?: \(näidatud \d+\))?\*[ \t]*$")
PLOT = re.compile(r"^!\[väljund\]\(/pildid/[0-9a-f]{40}\.png\)[ \t]*$")
FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*([^\s`]*)")
HEADING = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
DELIMITER = re.compile(r"^\s*\|?\s*:?-+:?\s*(\|\s*:?-+:?\s*)*\|?\s*$")
INLINE = re.compile(r"`=[ \t]*([^`⇒]+?)[ \t]*(?:⇒[^`]*)?`")
ARROW = "⇒"


def parse(markdown):
    """{lines, cells: [{start, end, lang, code, output: (start, end) | None}], tables: [{start, end,
    heading, header, rows}], inline: [{line, start, end, expr}]}. A cell's output is its output
    region. Line numbers index `lines`; end is exclusive."""
    lines = markdown.split("\n")
    cells, tables, inline, heading = [], [], [], None
    i = 0
    while i < len(lines):
        line = lines[i]
        fence = FENCE.match(line)
        if fence:
            end = _fence_end(lines, i, fence.group(1))
            lang = fence.group(2).lower()
            if lang in CELL_LANGS:
                region = _region(lines, end)
                cells.append({"start": i, "end": end, "lang": lang, "code": "\n".join(lines[i + 1:end - 1]),
                              "output": region})
                i = region[1] if region else end
                continue
            i = end
            continue
        m = HEADING.match(line)
        if m:
            heading = m.group(2).strip()
        elif "|" in line and i + 1 < len(lines) and DELIMITER.match(lines[i + 1]) and "|" in lines[i] + lines[i + 1]:
            end = i + 2
            while end < len(lines) and lines[end].strip() and "|" in lines[end]:
                end += 1
            header = split_row(line)
            rows = [split_row(r) for r in lines[i + 2:end]]
            tables.append({"start": i, "end": end, "heading": heading, "header": header,
                           "rows": [r[:len(header)] + [""] * (len(header) - len(r)) for r in rows]})
            for j in range(i, end):
                inline.extend(_inline(lines[j], j))
            i = end
            continue
        inline.extend(_inline(line, i))
        i += 1
    return {"lines": lines, "cells": cells, "tables": tables, "inline": inline}


def _fence_end(lines, start, mark):
    for j in range(start + 1, len(lines)):
        stripped = lines[j].strip()
        if stripped.startswith(mark[0] * len(mark)) and set(stripped) <= {mark[0]}:
            return j + 1
    return len(lines)


def _region(lines, start):
    """(first line, end) of the output region that follows a cell ending at `start`, or None."""
    j, first, last = start, None, None
    while True:
        k = j
        while k < len(lines) and not lines[k].strip():
            k += 1
        if k >= len(lines):
            break
        fence = FENCE.match(lines[k])
        if fence and fence.group(2).lower() in (OUTPUT_LANG, ERROR_LANG):
            end = _fence_end(lines, k, fence.group(1))
        elif PLOT.match(lines[k]):
            end = k + 1
        elif CAPTION.match(lines[k]):
            end = k + 1
            t = end
            while t < len(lines) and not lines[t].strip():
                t += 1
            if t + 1 < len(lines) and "|" in lines[t] and DELIMITER.match(lines[t + 1]):
                end = t + 2
                while end < len(lines) and lines[end].strip() and "|" in lines[end]:
                    end += 1
        else:
            break
        first = k if first is None else first
        last = j = end
    return (first, last) if first is not None else None


def _inline(line, number):
    return [{"line": number, "start": m.start(), "end": m.end(), "expr": m.group(1).strip()} for m in INLINE.finditer(line)]


def split_row(line):
    """'| a | b \\| c |' -> ['a', 'b | c']."""
    cells, current, escaped = [], "", False
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|") and not text.endswith("\\|"):
        text = text[:-1]
    for ch in text:
        if escaped:
            current += ch if ch == "|" else "\\" + ch
            escaped = False
        elif ch == "\\":
            escaped = True
        elif ch == "|":
            cells.append(current.strip())
            current = ""
        else:
            current += ch
    if escaped:
        current += "\\"
    cells.append(current.strip())
    return cells


def request(doc, upto=None):
    """What the sandbox gets: the cells (up to and including cell `upto`), every table, the inline
    expressions."""
    cells = doc["cells"] if upto is None else doc["cells"][:upto + 1]
    return {"cells": [c["code"] for c in cells],
            "tables": [{"heading": t["heading"], "header": t["header"], "rows": t["rows"]} for t in doc["tables"]],
            "inline": [x["expr"] for x in doc["inline"]] if upto is None else []}


def inputs_hash(doc):
    """What a run depends on, without the outputs: unchanged inputs need no automatic run."""
    data = {"cells": [c["code"] for c in doc["cells"]], "tables": [[t["header"], t["rows"]] for t in doc["tables"]],
            "inline": [x["expr"] for x in doc["inline"]]}
    return hashlib.sha256(json.dumps(data, ensure_ascii=False).encode()).hexdigest()[:16]


def apply(doc, answer, upto=None):
    """The Markdown with the answer written in: outputs under the cells that ran, changed tables,
    inline values. Edits go bottom-up so line numbers stay valid."""
    lines = list(doc["lines"])
    edits = []  # (start, end, new lines)
    ran = doc["cells"] if upto is None else doc["cells"][:upto + 1]
    results = answer.get("cells") or []
    for cell, result in zip(ran, results):
        block = region_lines(result)
        if cell["output"]:
            start, end = cell["output"]
            edits.append((cell["end"], end, [""] + block if block else []))
        elif block:
            edits.append((cell["end"], cell["end"], ["", *block]))
    for changed in answer.get("tables") or []:
        t = doc["tables"][changed["index"]]
        edits.append((t["start"], t["end"], render_table(changed["header"], changed["rows"])))
    values = answer.get("inline") or []
    by_line = {}
    for spot, value in zip(doc["inline"], values):
        by_line.setdefault(spot["line"], []).append((spot, value))
    table_lines = {j for e in edits for j in range(e[0], e[1])}
    for number, spots in by_line.items():
        if number in table_lines:
            continue  # a rewritten table: its inline values wait for the next run
        line = lines[number]
        for spot, value in sorted(spots, key=lambda s: -s[0]["start"]):
            shown = value.get("value", "") if "error" not in value else f"viga: {value['error']}"
            shown = " ".join(str(shown).replace("`", "'").split())[:200]
            line = f"{line[:spot['start']]}`= {spot['expr']} {ARROW} {shown}`{line[spot['end']:]}"
        edits.append((number, number + 1, [line]))
    for start, end, new in sorted(edits, key=lambda e: (e[0], e[1]), reverse=True):
        lines[start:end] = new
    return "\n".join(lines)


def output_block(text, lang=OUTPUT_LANG):
    """A ```väljund (or ```viga) fence holding text; the fence is longer than any backtick run inside."""
    longest = max((len(run) for run in re.findall(r"`+", text)), default=0)
    fence = "`" * max(3, longest + 1)
    return [f"{fence}{lang}", *text.split("\n"), fence]


def region_lines(result):
    """A cell's output region: printed text, its tables, its plots (`images`: stored picture names),
    then the error; blocks apart by blank lines. [] when there is nothing to show."""
    blocks = []
    text = (result.get("out") or "").rstrip("\n")
    if text:
        blocks.append(output_block(text))
    for table in result.get("tables") or []:
        rows, columns = table.get("total") or [len(table["rows"]), len(table["header"])]
        name = re.sub(r"[*_`|\[\]\\<>]", "", str(table.get("name") or "")).strip()
        shown = f" (näidatud {len(table['rows'])})" if len(table["rows"]) < rows else ""
        caption = f"*Tabel{': ' + name if name else ''} · {rows} rida × {columns} veergu{shown}*"
        blocks.append([caption, "", *render_table(table["header"], table["rows"])] if table["header"] else [caption])
    for picture in result.get("images") or []:
        if re.fullmatch(r"[0-9a-f]{40}\.png", str(picture)):
            blocks.append([f"![väljund](/pildid/{picture})"])
    if result.get("error"):
        blocks.append(output_block(str(result["error"]), ERROR_LANG))
    out = []
    for block in blocks:
        out += ([""] if out else []) + block
    return out


def render_table(header, rows):
    def cell(value):
        return " ".join(str(value).split()).replace("|", "\\|")
    out = ["| " + " | ".join(cell(h) for h in header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    out += ["| " + " | ".join(cell(v) for v in row) + " |" for row in rows]
    return out


def mouser_links(doc, is_mouser):
    """Every table cell that is a Mouser link or part number, in note order, once."""
    seen = []
    for t in doc["tables"]:
        for row in t["rows"]:
            for value in row:
                if is_mouser(value) and value not in seen:
                    seen.append(value)
    return seen
