"""Python cells: the sandbox's kernel runs here as a local process (no container, no limits), so the
app's side and the kernel are tested together. The container itself: test_arv_sandbox.py, -m docker."""

import json
import pathlib
import subprocess
import sys

import pytest

from app.arvuta import client

KERNEL = pathlib.Path(__file__).resolve().parents[2] / "sandbox" / "kernel.py"


def run_kernel(request, seconds=10):
    out = subprocess.run([sys.executable, str(KERNEL)], input=json.dumps(request), capture_output=True, text=True,
                         timeout=seconds + 20)
    return json.loads(out.stdout.rstrip("\n").rsplit("\n", 1)[-1])


@pytest.fixture
def local_sandbox(monkeypatch):
    """client.send runs the kernel locally; .requests holds what was sent."""
    sent = []

    def send(request, seconds):
        sent.append(request)
        return {**run_kernel(request, seconds), "seconds": 0.1}
    monkeypatch.setattr(client, "send", send)
    send.requests = sent
    return send
