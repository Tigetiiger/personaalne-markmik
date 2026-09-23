"""JSON API for notes, links, tasks and search (/api/notes/..., /api/search).

Owner: WP-B (notes core). Stub from Wave 0; see docs/CONTRACTS.md."""

from flask import Blueprint

bp = Blueprint("notes_api", __name__)
