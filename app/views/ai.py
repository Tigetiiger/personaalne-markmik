"""Assistant chat and inline AI (/api/ai/...), streamed as SSE.

Owner: WP-F (AI assistant). Stub from Wave 0; see docs/CONTRACTS.md."""

from flask import Blueprint

bp = Blueprint("ai", __name__)
