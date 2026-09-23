"""Uploaded SVG: keep the drawing, drop anything that could run code when the file is opened
directly (scripts, event handlers, javascript: links, foreignObject). The response also carries
`Content-Security-Policy: sandbox`, so a missed case still cannot touch the site."""

import html
import logging
import re
import xml.etree.ElementTree as ET

log = logging.getLogger(__name__)

_SVG_NS = "http://www.w3.org/2000/svg"
_XLINK = "{http://www.w3.org/1999/xlink}href"
_DROP = {"script", "foreignObject", "set", "animate", "animateMotion", "animateTransform", "handler"}
_SAFE_HREF = re.compile(r"^\s*(#|(https?:)?//|/|\.|data:image/)", re.I)
# HTML entities (&nbsp; …) that XML does not know; the five XML ones stay as they are.
_XML_ENTITY = re.compile(r"&(amp|lt|gt|quot|apos);", re.I)
_HTML_ENTITY = re.compile(r"&(?!(amp|lt|gt|quot|apos|#\w+);)(\w+);", re.I)
_STYLE_EXTERNAL = re.compile(r"@import[^;]*;|url\(\s*(['\"]?)(?!data:)[^)]*\)", re.I)
ET.register_namespace("", "http://www.w3.org/2000/svg")
ET.register_namespace("xlink", "http://www.w3.org/1999/xlink")


def _local(tag):
    return tag.rsplit("}", 1)[-1]


_WHITE = {"#fff", "#ffffff", "white", "rgb(255,255,255)", "rgb(255, 255, 255)"}


def _drop_background(root):
    """Excalidraw and friends export a canvas-sized white <rect> under the drawing: on the dark
    theme the picture would be a white slab. Without it the drawing is transparent."""
    size = root.get("viewBox", "").split()
    width, height = (size[2], size[3]) if len(size) == 4 else (root.get("width", ""), root.get("height", ""))
    for child in list(root):
        if _local(child.tag) == "rect" and child.get("fill", "").strip().lower() in _WHITE \
                and child.get("x", "0") == "0" and child.get("y", "0") == "0" \
                and child.get("width") == width and child.get("height") == height:
            root.remove(child)
            return


def clean(data):
    """Sanitised SVG bytes, or None if the file is not an SVG document."""
    text = data.decode("utf-8-sig", "replace")
    # Entity definitions can expand without bound (billion laughs); a drawing does not need them.
    # Inkscape and Illustrator write a plain <!DOCTYPE svg PUBLIC …> line: that one just goes.
    if re.search(r"<!ENTITY|<!DOCTYPE[^>]*\[", text, re.I):
        log.warning("SVG rejected: internal DOCTYPE subset or ENTITY")
        return None
    text = re.sub(r"<!DOCTYPE[^>]*>", "", text, flags=re.I)
    # XML knows only the five core entities, case-sensitively: "&LT;" must stay text ("&lt;"),
    # not become a real "<"; other HTML entities (&nbsp; …) become their characters.
    text = _XML_ENTITY.sub(lambda m: f"&{m.group(1).lower()};", text)
    text = _HTML_ENTITY.sub(lambda m: html.unescape(m.group(0)), text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as e:
        log.warning("SVG rejected: %s", e)
        return None
    if _local(root.tag) != "svg":
        log.warning("SVG rejected: root element is %s", root.tag)
        return None
    for parent in root.iter():
        for child in list(parent):
            if _local(child.tag) in _DROP:
                parent.remove(child)
    _drop_background(root)
    for el in root.iter():
        for name in list(el.attrib):
            value = el.attrib[name]
            if _local(name).lower().startswith("on"):
                del el.attrib[name]
            elif name in ("href", _XLINK) and not _SAFE_HREF.match(value):
                del el.attrib[name]
    for el in root.iter():
        if _local(el.tag) == "style" and el.text:
            el.text = _STYLE_EXTERNAL.sub("", el.text)  # no fetches from other sites when opened directly
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)
