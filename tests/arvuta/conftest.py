"""Python cells: the sandbox's kernel runs here as a local process (no container, no limits), so the
app's side and the kernel are tested together. The container itself: test_arv_sandbox.py, -m docker."""

import pytest

from app.arvuta import client
from arv_kernel import run_kernel


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
