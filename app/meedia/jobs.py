"""Worker jobs of the media folder. Converting a video can take as long as the video, so it runs
in one converter thread of its own: the worker's loop (reminders, sync) never waits for ffmpeg."""

import logging
import os
import queue
import threading

from ..db import connect
from ..jobs import job
from . import index, probe, store

log = logging.getLogger("markmik.meedia")

_queue = queue.Queue()
_busy = set()  # names queued or being converted; the scan leaves them alone
_thread = None


@job("meedia-scan", every=120)
def scan(conn):
    counts = index.scan(conn, store.default_root(), convert=_submit, busy=frozenset(_busy))
    if any(v for k, v in counts.items() if k not in ("waiting", "busy", "failed")):
        log.info("media scan: %s", counts)
    return counts


@job("meedia-trash", at=("04:00",))
def purge_trash(conn):
    root = store.default_root()
    return store.purge_trash(root) + store.purge_incoming(root)


def _submit(name, source):
    global _thread
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
