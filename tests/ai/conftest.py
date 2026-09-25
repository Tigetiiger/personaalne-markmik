"""Real-mode runner tests with the SDK's query() replaced: they see the options a real call would
get and feed back scripted SDK messages. No model is ever called here."""

import asyncio
import os

import pytest

from app.ai import runner


class Scripted:
    """query() stand-in. reply(...) sets what the next calls yield; calls[i] = {prompt, options,
    cwd_files}; `closed` turns true when the generator was finished or cancelled."""

    def __init__(self):
        self.calls = []
        self.messages = []
        self.error = None
        self.delay = 0
        self.closed = False
        self.before = None  # (options) -> None, called as the CLI would start

    def reply(self, *messages, error=None, delay=0):
        self.messages, self.error, self.delay = list(messages), error, delay

    @property
    def options(self):
        return self.calls[-1]["options"]

    async def query(self, *, prompt, options):
        self.calls.append({"prompt": prompt, "options": options, "cwd_files": os.listdir(options.cwd)})
        if self.before:
            self.before(options)
        try:
            for msg in self.messages:
                if self.delay:
                    await asyncio.sleep(self.delay)
                if callable(msg):  # async (options) -> message, run where the SDK runs
                    msg = await msg(options)
                yield msg
            if self.error:
                raise self.error
        finally:
            self.closed = True


@pytest.fixture
def real_mode(monkeypatch):
    """AI_FAKE off, a dummy token in the env (the vault falls back to it), query() scripted."""
    monkeypatch.delenv("AI_FAKE")
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "sk-test-token")
    scripted = Scripted()
    monkeypatch.setattr(runner, "query", scripted.query)
    return scripted

