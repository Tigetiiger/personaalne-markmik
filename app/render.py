"""Note Markdown -> HTML for pages outside the editor (share view, anywhere else). Always
sanitised: a shared note may carry a guest's HTML, and it is shown on this site."""

import html
import re
import threading

import markdown
import nh3

# The editor (MarkText's Muya) writes GFM plus MarkText extras; these cover the same syntax:
# ~~strike~~, - [x] tasks, [^1] footnotes, $inline$ / $$block$$ math (typeset by KaTeX in the browser).
EXTENSIONS = [
    "tables", "fenced_code", "attr_list", "md_in_html", "sane_lists", "footnotes",
    "pymdownx.tilde", "pymdownx.tasklist", "pymdownx.arithmatex",
]
EXTENSION_CONFIGS = {
    # Muya's superSubScript is off, so a lone ~ is ordinary text, not subscript.
    "pymdownx.tilde": {"subscript": False},
    "pymdownx.arithmatex": {"generic": True},
}

# Python-Markdown emits ```mermaid as an escaped <code>; Mermaid wants the text in <pre class="mermaid">.
# The code is HTML-escaped, so it has no "<": `[^<]*` cannot backtrack (an unclosed tag with
# `.*?` rescanned the rest of the page for every occurrence).
MERMAID = re.compile(r'<pre><code class="language-mermaid">([^<]*)</code></pre>')
# The editor's image toolbar aligns a picture by writing it as <img … data-align="right">. On the
# page it becomes <figure class="aligned aligned-right">: beside the text on a wide screen, full
# width on a narrow one; the image title is the caption. A picture alone in its paragraph is
# lifted out of it (a figure may not sit inside <p>).
# {0,2000}: a tag is never that long; unbounded `[^>]*` on an unclosed tag was quadratic.
ALIGNED = r'<img\b(?=[^>]{0,2000}+>)[^>]*\bdata-align="(?:left|right|center)"[^>]*>'
ALONE_IN_P = re.compile(rf"<p>\s*({ALIGNED})\s*</p>")
ALIGNED_IMAGE = re.compile(rf"(?<!<figure class=\"aligned aligned-)({ALIGNED})")
_ALIGN = re.compile(r'\bdata-align="(left|right|center)"')
_TITLE = re.compile(r'\btitle="([^"]*)"')


def _figure(match):
    img = match.group(1)
    align = _ALIGN.search(img).group(1)
    title = _TITLE.search(img)
    caption = f"<figcaption>{title.group(1)}</figcaption>" if title and title.group(1).strip() else ""
    return f'<figure class="aligned aligned-{align}">{img}{caption}</figure>'


# A Markdown table scrolls in its own box instead of widening the page. Python-Markdown's table
# is always exactly "<table>"; found with str.find so an unclosed raw <table> cannot make a
# regex rescan the rest of the page for every table.
def _wrap_tables(out):
    parts, pos = [], 0
    while True:
        start = out.find("<table>", pos)
        end = out.find("</table>", start) if start != -1 else -1
        if end == -1:
            parts.append(out[pos:])
            return "".join(parts)
        end += len("</table>")
        parts.append(out[pos:start])
        parts.append(f'<div class="tablewrap">{out[start:end]}</div>')
        pos = end


# Callouts as on GitHub: a quote whose first line is [!NOTE], [!TIP], [!WARNING] or [!CAUTION]
# becomes a titled box.
CALLOUTS = {"NOTE": "Märkus", "TIP": "Nõuanne", "WARNING": "Hoiatus", "CAUTION": "Ettevaatust"}
_CALLOUT = re.compile(r"<blockquote>\s*<p>\[!(NOTE|TIP|WARNING|CAUTION)\][ \t]*(?:</p>|\n|$)")
_BLOCKQUOTE = re.compile(r"<(/?)blockquote>")


def _callouts(out):
    parts, pos = [], 0
    for m in _CALLOUT.finditer(out):
        if m.start() < pos:
            continue  # inside a callout already converted
        # The matching </blockquote>: a quote may hold another quote.
        depth, end = 0, None
        for q in _BLOCKQUOTE.finditer(out, m.start()):
            depth += -1 if q.group(1) else 1
            if depth == 0:
                end = q
                break
        if end is None:
            break  # unclosed quote: it runs to the end, and scanning again for each later one was quadratic
        kind = m.group(1)
        body = out[m.end():end.start()]
        # "[!NOTE]" alone in its paragraph closed it; else the text went on in the same one.
        body = body if m.group(0).endswith("</p>") else "<p>" + body
        parts += [
            out[pos:m.start()],
            f'<div class="callout callout-{kind.lower()}" role="note"><p class="callout-title">{CALLOUTS[kind]}</p>',
            body.strip(),
            "</div>",
        ]
        pos = end.end()
    parts.append(out[pos:])
    return "".join(parts)


# Code fence: opens with 3+ backticks or tildes (up to 3 spaces in), closes only with the same
# character and at least the same length (CommonMark).
_FENCE = re.compile(r"^ {0,3}(`{3,}|~{3,})")
_ATX = re.compile(r"^ {0,3}#{1,6}(?:[ \t]|$)")


def _heading_blocks(md):
    """A blank line after every ATX heading outside code. It renders the same, but the tables
    extension re-splits the whole remaining block for each heading in a run without blank lines:
    quadratic (20 000 such lines took 20 s)."""
    out, fence = [], None
    for line in md.split("\n"):
        out.append(line)
        m = _FENCE.match(line)
        if m and (fence is None or (m.group(1)[0] == fence[0] and len(m.group(1)) >= len(fence))):
            fence = m.group(1) if fence is None else None
        elif fence is None and _ATX.match(line):
            out.append("")
    return "\n".join(out)


# Classes this module and its Markdown extensions write; a note's own (raw HTML, {: .x}) could
# borrow the page's styles.
CLASSES = {
    "task-list", "task-list-item", "footnote", "footnote-ref", "footnote-backref", "arithmatex",
    "tablewrap", "mermaid", "callout", "callout-note", "callout-tip", "callout-warning",
    "callout-caution", "callout-title", "aligned", "aligned-left", "aligned-right", "aligned-center",
}
_LANGUAGE = re.compile(r"^language-[\w+#.-]{1,40}$")


def _attribute_filter(tag, attr, value):
    # Footnotes link to their own ids ("fn:1", "fnref:1"); any other id could clobber the page's own.
    if attr == "id" and not value.startswith("fn"):
        return None
    if attr == "class":
        kept = [c for c in value.split() if c in CLASSES or (tag == "code" and _LANGUAGE.match(c))]
        return " ".join(kept) or None
    return value


_CLEANER = nh3.Cleaner(
    tags={
        "a", "abbr", "b", "blockquote", "br", "code", "dd", "del", "details", "div", "dl", "dt", "em",
        "figcaption", "figure", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "i", "img", "input", "kbd",
        "li", "mark", "ol", "p", "pre", "s", "span", "strong", "sub", "summary", "sup", "table",
        "tbody", "td", "tfoot", "th", "thead", "tr", "u", "ul",
    },
    attributes={
        "*": {"class", "title"},
        "a": {"href"},
        "img": {"src", "alt", "width", "height", "data-align"},
        "td": {"style"},
        "th": {"style"},
        "ol": {"start"},
        "li": {"id"},
        "sup": {"id"},
        "details": {"open"},
    },
    tag_attribute_values={"input": {"type": {"checkbox"}, "checked": {"", "checked"}}, "div": {"role": {"note"}}},
    set_tag_attribute_values={"input": {"disabled": "disabled"}},
    attribute_filter=_attribute_filter,
    filter_style_properties={"text-align"},
    url_schemes={"http", "https", "mailto"},
)

# One converter per thread, reset per call: building it anew each time costs ~0.4 ms, and a
# converter is not thread-safe (gunicorn serves with threads: guests' pages would mix).
_local = threading.local()


def _converter():
    if not hasattr(_local, "md"):
        _local.md = markdown.Markdown(extensions=EXTENSIONS, extension_configs=EXTENSION_CONFIGS)
    return _local.md


def render_markdown(md):
    converter = _converter()
    converter.reset()
    out = converter.convert(_heading_blocks(md))
    out = MERMAID.sub(lambda m: f'<pre class="mermaid">{html.unescape(m.group(1))}</pre>', out)
    out = _callouts(_wrap_tables(out))
    out = ALIGNED_IMAGE.sub(_figure, ALONE_IN_P.sub(r"\1", out))
    return _CLEANER.clean(out)
