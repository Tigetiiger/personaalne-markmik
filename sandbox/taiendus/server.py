"""The taiendus container's server (docs/TAIENDUS-PLAN.md): completion requests as one JSON line per
connection on a Unix socket, answered by engine.py in one long-lived process (Jedi is fast only when
warm: 3.8 s cold, 0.09 s warm). Requests are handled one at a time; Jedi only reads the code."""

import json
import os
import socket
import sys
import threading

import jedi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # python -I leaves this folder out
import engine  # noqa: E402

SOCKET = os.environ.get("TAIENDUS_SOCKET", "/run/taiendus/taiendus.sock")
REQUEST_CAP = 512 * 1024
lock = threading.Lock()


def answer(data):
    try:
        request = json.loads(data)
        if not isinstance(request, dict):
            raise ValueError
    except ValueError:
        return {"error": "vigane päring"}
    with lock:
        try:
            return engine.handle(request)
        except Exception as exc:  # noqa: BLE001 - Jedi on odd code: an error answer, the server lives
            return {"error": f"täiendus ebaõnnestus: {type(exc).__name__}"}


def serve(conn):
    with conn:
        conn.settimeout(10)
        data = b""
        try:
            while not data.endswith(b"\n"):
                chunk = conn.recv(65536)
                if not chunk:
                    return
                data += chunk
                if len(data) > REQUEST_CAP:
                    conn.sendall(b'{"error": "liiga suur"}\n')
                    return
            conn.sendall(json.dumps(answer(data), ensure_ascii=False).encode() + b"\n")
        except OSError:
            pass


def main():
    jedi.settings.cache_directory = os.environ.get("JEDI_CACHE", "/tmp/jedi")
    # Warm up: pandas' first analysis is the slow one.
    engine.handle({"kind": "complete", "before": ['t = tabel("x")'], "code": "t.so", "line": 1, "col": 4})
    try:
        os.unlink(SOCKET)
    except FileNotFoundError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET)
    os.chmod(SOCKET, 0o666)  # web runs as another user; only web shares the socket's directory
    server.listen(16)
    print("taiendus valmis", file=sys.stderr, flush=True)
    while True:
        conn, _ = server.accept()
        threading.Thread(target=serve, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()
