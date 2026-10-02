"""Worker jobs of the media folder. Converting a video can take as long as the video, so it runs
in one converter thread of its own: the worker's loop (reminders, sync) never waits for ffmpeg."""

import logging
import os
import queue
import threading

from ..db import connect
from ..jobs import job
from . import index, probe, store, tagging

log = logging.getLogger("markmik.meedia")

_queue = queue.Queue()
_busy = set()  # names queued or being converted; the scan leaves them alone
_busy_lock = threading.Lock()  # the converter thread removes names while the scan copies the set
_thread = None


@job("meedia-scan", every=120)
def scan(conn):
    with _busy_lock:
        busy = frozenset(_busy)
    counts = index.scan(conn, store.default_root(), convert=_submit, busy=busy)
    if any(v for k, v in counts.items() if k not in ("waiting", "busy", "failed")):
        log.info("media scan: %s", counts)
    return counts


@job("meedia-tag", every=180)
def tag(conn):
    # A few calls a run (~5 s each), so the worker's other jobs wait at most about half a minute.
    return tagging.run(conn, store.default_root(), max_calls=4)


@job("meedia-trash", at=("04:00",))
def purge_trash(conn):
    root = store.default_root()
    return store.purge_trash(root) + store.purge_incoming(root)


def _submit(name, source):
    global _thread
    with _busy_lock:
        _busy.add(name)
    _queue.put((name, source))
    if _thread is None or not _thread.is_alive():
        _thread = threading.Thread(target=_convert_forever, name="meedia-convert", daemon=True)
        _thread.start()


def _convert_forever():
    conn = connect(os.environ.get("DATABASE_PATH", "data/markmik.db"))
    while True:
        name, source = _queue.get()
        try:
            convert_one(conn, store.default_root(), name, source)
        finally:
            with _busy_lock:
                _busy.discard(name)


def convert_one(conn, root, name, source):
    try:
        final = index.intake(conn, root, name, source=source)
        log.info("media %s converted to %s", name, final)
    except probe.Unreadable as exc:
        index.fail(conn, name, str(exc))
    except Exception:  # noqa: BLE001 - one bad file must not stop the thread
        log.exception("media %s: conversion failed", name)
        index.fail(conn, name, "Teisendamine ebaõnnestus.")
