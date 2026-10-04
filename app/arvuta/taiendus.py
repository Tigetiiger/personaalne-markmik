"""The app's side of the taiendus container (sandbox/taiendus): one completion request over its Unix
socket. Tests replace send() with engine.py run in-process."""

import json
import os
import socket

SECONDS = 3
UNAVAILABLE = {"error": "Täiendus pole kättesaadav (konteiner taiendus)."}


def send(request):
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(SECONDS)
            s.connect(os.environ.get("TAIENDUS_SOCKET", "/run/taiendus/taiendus.sock"))
            s.sendall(json.dumps(request, ensure_ascii=False).encode() + b"\n")
            data = b""
            while not data.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                data += chunk
                if len(data) > 1024 * 1024:
                    return UNAVAILABLE
        answer = json.loads(data)
    except (OSError, ValueError):
        return UNAVAILABLE
    return answer if isinstance(answer, dict) else UNAVAILABLE
