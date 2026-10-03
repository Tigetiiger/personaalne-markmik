"""The app's side of the arvutus sandbox (sandbox/server.py): one JSON request over its Unix socket,
one JSON answer. Tests replace send() with a local run of sandbox/kernel.py."""

import json
import os
import socket

AUTO_SECONDS = 5
MANUAL_SECONDS = 30
ANSWER_CAP = 4 * 1024 * 1024
UNAVAILABLE = "Arvutusmootor pole kättesaadav (konteiner arvutus)."


def socket_path():
    return os.environ.get("ARVUTUS_SOCKET", "/run/arvutus/arvutus.sock")


def send(request, seconds):
    """The sandbox's answer, or {"error": ...} when it cannot be reached."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(seconds + 15)  # the server queues runs one at a time
            s.connect(socket_path())
            s.sendall(json.dumps({**request, "timeout": seconds}, ensure_ascii=False).encode() + b"\n")
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
                if len(data) > ANSWER_CAP:
                    return {"error": "Arvutuse väljund on liiga suur."}
    except (OSError, socket.timeout):
        return {"error": UNAVAILABLE}
    try:
        answer = json.loads(data)
    except ValueError:
        return {"error": UNAVAILABLE}
    return answer if isinstance(answer, dict) else {"error": UNAVAILABLE}
