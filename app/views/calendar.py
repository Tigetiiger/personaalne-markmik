"""Calendar pages and /api/events, /api/today, /ics/<token>.ics.

Owner: WP-D (calendar). Stub from Wave 0; see docs/CONTRACTS.md."""

from flask import Blueprint

bp = Blueprint("calendar", __name__)
