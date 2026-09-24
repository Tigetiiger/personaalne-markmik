"""Note text in the database <-> note file in the brain repo. Pure functions, no I/O.

In the database a body is Muya's Markdown: links are `[Title](/m/<slug>)`, images `/pildid/<name>`,
no frontmatter. In git a file is YAML frontmatter + the body with links as `[[<slug>|Title]]`
(gbrain resolves them by basename, `link_resolution.global_basename` on; docs/spikes/gbrain.md) and
images under `/notes/_pildid/`. Code spans and fences are never touched.

`from_file(to_file(body))` gives the body back exactly. Links that cannot be written as a wikilink
without losing text stay `/m/` links in the file: an anchor containing `]` (gbrain's wikilink
pattern stops at the first `]`), an empty anchor, and links with a title or spaces.
"""

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
    rf"(?<![!\\])\[\[(?P<target>[^\[\]|#\n]+?)(?P<frag>{_FRAG})?(?:\|(?P<alias>[^\]\n]*))?\]\]")
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


def from_file(text, *, note_slugs=()):
    """(frontmatter dict, body) from file text. note_slugs: slugs that are notes, so a bare
    `[[slug|T]]` becomes a /m/ link; wikilinks to other brain pages stay as they are."""
    text = text.replace("\r\n", "\n")
    meta, body = {}, text
    m = _FRONTMATTER_RE.match(text)
    if m:
        try:
            loaded = yaml.load(m.group(1), Loader=_Loader)
        except yaml.YAMLError:
            loaded = False  # not frontmatter: the whole text is body
        if loaded is None or isinstance(loaded, dict):
            meta, body = loaded or {}, text[m.end():]
            body = body[1:] if body.startswith("\n") else body
    return meta, body_from_file(body, note_slugs)


def body_to_file(body, basename_taken):
    body = _sub_outside_code(body, _IMG_DB_RE, lambda m, _: IMAGE_DIR)

    def wikilink(m, raw):
        anchor, slug, frag = raw(m, "anchor"), m["slug"], m["frag"] or ""
        if not anchor or "]" in anchor:
            return raw(m, 0)
        if slug in basename_taken:
            target = "/".join(p for p in ("notes", basename_taken[slug], slug) if p)
        else:
            target = slug
        return f"[[{target}{frag}]]" if anchor == slug else f"[[{target}{frag}|{anchor}]]"
    return _sub_outside_code(body, _MLINK_RE, wikilink)


def body_from_file(body, note_slugs=()):
    body = _sub_outside_code(body, _IMG_FILE_RE, lambda m, _: "/pildid/")

    def mlink(m, raw):
        target = m["target"]
        if target.startswith("notes/"):
            slug = target.rsplit("/", 1)[-1]
            is_note = not target.startswith("notes/_pildid/")
        else:
            slug, is_note = target, target in note_slugs
        if not is_note or not _SLUG_RE.fullmatch(slug):
            return raw(m, 0)
        alias = raw(m, "alias") if m["alias"] else slug  # [[x]] and [[x|]] both show x
        return f"[{alias}](/m/{slug}{m['frag'] or ''})"
    return _sub_outside_code(body, _WIKI_RE, mlink)


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
