"""Note text in the database <-> note file in the brain repo. Pure functions, no I/O.

In the database a body is Muya's Markdown: links are `[Title](/m/<slug>)`, images `/pildid/<name>`,
no frontmatter. In git a file is YAML frontmatter + the body with links as `[[<slug>|Title]]`
(gbrain resolves them by basename, `link_resolution.global_basename` on; docs/spikes/gbrain.md) and
images under `/notes/_pildid/`. Code spans and fences are never touched.

`from_file(to_file(body))` gives the body back exactly. The rule does not depend on which notes
exist: every wikilink whose target is slug-shaped (`[[x]]`, `[[x|T]]`, `[[notes/.../x]]`) is an app
link, dangling or not, so a link survives its target being deleted and created again. A literal
slug-shaped `[[x]]` typed in the app is written as `\\[[x]]` (one more backslash in front of any
run of them) and comes back as text. Links that cannot be written as a wikilink without losing
text stay `/m/` links in the file: an anchor containing `]` (gbrain's wikilink pattern stops at the
first `]`) or `[` (a wikilink alias never holds one, so that `[[` earlier on the line cannot swallow
it), an empty anchor, links with a title or spaces.
"""

import base64
import json
import re

import yaml

from . import notes, util

KNOWN_KEYS = ("title", "type", "tags", "aliases", "date", "updated", "source", "id")
IMAGE_DIR = "/notes/_pildid/"

# Same link text as app/links.py: escaped brackets and one level of balanced ones.
_TEXT = r"(?:[^\[\]\\\n]|\\.|\[(?:[^\[\]\\\n]|\\.)*\])*"
_FRAG = r"#[^)\s\[\]|]*"
_MLINK_RE = re.compile(rf"(?<![!\\])\[(?P<anchor>{_TEXT})\]\(/m/(?P<slug>[a-z0-9-]+)(?P<frag>{_FRAG})?\)")
_WIKI_RE = re.compile(
    rf"(?<![!\\])(?P<esc>\\*)\[\[(?P<target>[^\[\]|#\n]+?)(?P<frag>{_FRAG})?(?:\|(?P<alias>[^\[\]\n]*))?\]\]")
_SLUG_RE = re.compile(r"[a-z0-9-]+")
# A path, not part of a longer one (https://x.ee/pildid/ stays).
_IMG_DB_RE = re.compile(r"(?<![\w/.~-])/pildid/(?=[A-Za-z0-9])")
_IMG_FILE_RE = re.compile(r"(?<![\w/.~-])/notes/_pildid/(?=[A-Za-z0-9])")
_IMG_NAME_RE = re.compile(r"(?<![\w/.~-])/pildid/([A-Za-z0-9][A-Za-z0-9._-]*)")
_FRONTMATTER_RE = re.compile(r"\A---[ \t]*\n(.*?)^(?:---|\.\.\.)[ \t]*(?:\n|\Z)", re.S | re.M)


# YAML without timestamp resolution: `date: 2026-09-24` stays the string it was, in both
# directions, so frontmatter written on the laptop keeps its look after a round trip.
class _Loader(yaml.SafeLoader):
    pass


class _Dumper(yaml.SafeDumper):
    pass


for _cls in (_Loader, _Dumper):
    _cls.yaml_implicit_resolvers = {
        k: [r for r in v if r[0] != "tag:yaml.org,2002:timestamp"]
        for k, v in _cls.yaml_implicit_resolvers.items()}


def to_file(note, body, *, basename_taken=None):
    """File text for a note row. basename_taken: {slug: folder} of link targets whose file name
    another .md in the repo also has; those links get the full path form."""
    meta = {
        "title": note["title"],
        "type": "note",
        "tags": json.loads(note["tags_json"]),
    }
    if aliases := json.loads(note["aliases_json"]):
        meta["aliases"] = aliases
    meta.update(date=_local_day(note["created_at"]), updated=_local_day(note["updated_at"]),
                source="markmik", id=note["id"])
    for key, value in json.loads(note["frontmatter_extra"] or "{}").items():
        meta.setdefault(key, value)
    front = yaml.dump(meta, Dumper=_Dumper, allow_unicode=True, sort_keys=False,
                      default_flow_style=None, width=1_000_000)
    return f"---\n{front}---\n\n{body_to_file(body, basename_taken or {})}"


def from_file(text):
    """(frontmatter dict, body) from file text. Wikilinks to paths outside notes/
    (`[[pages/x]]`) and to names that are not slugs stay as they are."""
    text = text.replace("\r\n", "\n")
    meta, body = {}, text
    m = _FRONTMATTER_RE.match(text)
    if m:
        try:
            # Plain JSON values only: the extras are stored as JSON. Keys that are not scalars
            # are dropped.
            loaded = json.loads(json.dumps(yaml.load(m.group(1), Loader=_Loader), default=_json_value,
                                           skipkeys=True))
        except (yaml.YAMLError, ValueError, TypeError, RecursionError):
            loaded = False  # not frontmatter: the whole text is body
        if loaded is None or isinstance(loaded, dict):
            meta, body = loaded or {}, text[m.end():]
            body = body[1:] if body.startswith("\n") else body
    return meta, body_from_file(body)


def _json_value(value):
    """!!set -> sorted list, !!binary -> base64 text, anything else (!!timestamp) -> its text."""
    if isinstance(value, (set, frozenset)):
        return sorted(value, key=str)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode()
    return str(value)


def body_to_file(body, basename_taken):
    body = _sub_outside_code(body, _IMG_DB_RE, lambda m, _: IMAGE_DIR)
    # Literal wikilinks that from_file would turn into links get one more backslash.
    body = _sub_outside_code(body, _WIKI_RE, lambda m, raw: "\\" + raw(m, 0)
                             if _note_target(m["target"]) else raw(m, 0))

    def wikilink(m, raw):
        anchor, slug, frag = raw(m, "anchor"), m["slug"], m["frag"] or ""
        if not anchor or "[" in anchor or "]" in anchor:
            return raw(m, 0)
        if slug in basename_taken:
            target = "/".join(p for p in ("notes", basename_taken[slug], slug) if p)
        else:
            target = slug
        return f"[[{target}{frag}]]" if anchor == slug else f"[[{target}{frag}|{anchor}]]"
    return _sub_outside_code(body, _MLINK_RE, wikilink)


def body_from_file(body):
    body = _sub_outside_code(body, _IMG_FILE_RE, lambda m, _: "/pildid/")

    def mlink(m, raw):
        slug = _note_target(m["target"])
        if slug is None:
            return raw(m, 0)
        if m["esc"]:  # a literal written by to_file (or escaped on the laptop): one backslash less
            return raw(m, 0)[1:]
        alias = raw(m, "alias") if m["alias"] else slug  # [[x]] and [[x|]] both show x
        return f"[{alias}](/m/{slug}{m['frag'] or ''})"
    return _sub_outside_code(body, _WIKI_RE, mlink)


def _note_target(target):
    """The note slug a wikilink target names: a bare slug, or the file name of a path under notes/."""
    slug = target.rsplit("/", 1)[-1] if target.startswith("notes/") else target
    return slug if _SLUG_RE.fullmatch(slug) else None


def image_names(body):
    """Names of /pildid/ images the body refers to outside code."""
    return {m.group(1) for m in _IMG_NAME_RE.finditer(notes.mask_code(body))}


def _sub_outside_code(text, pattern, repl):
    """pattern.sub on the text with code masked; repl(match, raw) builds the replacement, and
    raw(match, group) returns that group from the original text (the mask blanks code inside)."""
    masked = notes.mask_code(text)

    def raw(m, group):
        return text[m.start(group):m.end(group)]
    out, pos = [], 0
    for m in pattern.finditer(masked):
        out.append(text[pos:m.start()])
        out.append(repl(m, raw))
        pos = m.end()
    out.append(text[pos:])
    return "".join(out)


def _local_day(stamp):
    return util.parse_iso(stamp).astimezone(util.TALLINN).date().isoformat()
