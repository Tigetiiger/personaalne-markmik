"""STUB (Wave 0). WP-B replaces this whole file; signatures in docs/CONTRACTS.md §4 etdates."""

from datetime import date


def parse_date(text, ref):
    try:
        return date.fromisoformat(text.strip())
    except ValueError:
        return None


def parse_time(text):
    return None


def find(text, ref):
    return []


def weekday_conflict(text, day):
    return False
