"""Rebuild the media index from the folder's companion files:
python -m app.meedia.reindex (inside the worker container)."""

import os

from ..db import connect, init_db
from . import index, store


def main():
    db_path = os.environ.get("DATABASE_PATH", "data/markmik.db")
    init_db(db_path)
    conn = connect(db_path)
    try:
        print(index.reindex(conn, store.default_root()))
    finally:
        conn.close()


if __name__ == "__main__":
    main()
