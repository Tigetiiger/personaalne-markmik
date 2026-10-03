"""The real arvutus container (pytest -m docker; ARVUTUS_SOCKET points at its socket): the attacks
from spike S6 end in a clean error, and the next run works."""

import os
import time

import pytest

from app.arvuta import client

pytestmark = pytest.mark.docker


@pytest.fixture(autouse=True)
def sandbox():
    if not os.path.exists(client.socket_path()):
        pytest.skip(f"no sandbox socket at {client.socket_path()}")


def run(code, seconds=5):
    return client.send({"cells": [code], "tables": [], "inline": [], "mouser": {}}, seconds)


def error(answer):
    return answer.get("error") or answer["cells"][0]["error"]


@pytest.mark.parametrize("code, expected", [
    ("import socket; socket.create_connection(('1.1.1.1', 80), timeout=2)", "Network is unreachable"),
    ("import urllib.request; urllib.request.urlopen('http://example.com', timeout=2)", "name resolution"),
    ("open('/sandbox/x', 'w')", "Read-only file system"),
    ("open('/proc/1/environ').read()", "Permission denied"),
    ("open('/proc/1/mem', 'rb').read(1)", "Permission denied"),
    ("x = bytearray(2 * 1024 ** 3)", "MemoryError"),
])
def test_attacks_fail(code, expected):
    assert expected in error(run(code))


def test_environment_holds_no_secrets():
    out = run("import os; sorted(os.environ)")["cells"][0]["out"]
    assert "KEY" not in out and "TOKEN" not in out and "PASSWORD" not in out


def test_time_limits():
    started = time.monotonic()
    assert "sekundiga" in error(run("import time; time.sleep(60)", 2))
    assert "katkes" in error(run("while True: pass", 2))
    assert time.monotonic() - started < 15


def test_nothing_survives_a_run():
    run("import os, time\nopen('/tmp/jaab', 'w').write('x')\nif os.fork() == 0:\n    os.setsid(); time.sleep(120); os._exit(0)")
    out = run("import os\nprint(sorted(os.listdir('/tmp')), len([p for p in os.listdir('/proc') if p.isdigit()]))")
    files, processes = out["cells"][0]["out"].rsplit(" ", 1)
    assert "jaab" not in files and int(processes) == 2  # the server and this run


def test_a_fork_bomb_does_not_stop_the_next_run():
    run("import os\nfor i in range(500):\n    try: os.fork()\n    except OSError: break")
    assert run("6 * 7")["cells"][0]["out"] == "42\n"
