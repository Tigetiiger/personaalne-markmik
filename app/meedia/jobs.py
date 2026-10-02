"""Worker jobs of the media folder."""

import logging

from ..jobs import job
from . import index, store

log = logging.getLogger("markmik.meedia")


@job("meedia-scan", every=120)
def scan(conn):
    counts = index.scan(conn, store.default_root())
    if any(v for k, v in counts.items() if k != "waiting"):
        log.info("media scan: %s", counts)
    return counts


@job("meedia-trash", at=("04:00",))
def purge_trash(conn):
    return store.purge_trash(store.default_root())
