"""gbrain/entrypoint.sh with a stub `gbrain` on PATH: setup reruns until every step went through,
and a start that gets no token fails instead of serving without one."""

import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

ENTRYPOINT = Path(__file__).resolve().parents[2] / "gbrain" / "entrypoint.sh"
TOKEN = "gbrain_abcdefghijklmnopqrstuvwxyz0123"
# Fails a step when a flag file of that name exists in $STUB; `serve` returns at once, so the
# script's sync loop ends and it exits.
STUB = f"""#!/bin/sh
echo "$*" >> "$STUB/log"
case "$1 $2" in
  "init "*) [ -e "$STUB/fail-init" ] && exit 1
            mkdir -p "$GBRAIN_HOME/.gbrain" && touch "$GBRAIN_HOME/.gbrain/config.json" ;;
  "sources set-path") [ -e "$STUB/fail-set-path" ] && exit 1 ;;
  "auth create") [ -e "$STUB/fail-auth" ] && exit 1
                 [ -e "$STUB/no-token" ] || echo "Token: {TOKEN}"
                 echo "Keep it safe." ;;
esac
exit 0
"""


@pytest.fixture
def box(tmp_path):
    (tmp_path / "bin").mkdir()
    (tmp_path / "bin" / "gbrain").write_text(STUB)
    (tmp_path / "bin" / "gbrain").chmod(0o755)
    (tmp_path / "brain" / ".git").mkdir(parents=True)
    (tmp_path / "home").mkdir()
    env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}", "STUB": str(tmp_path),
           "GBRAIN_HOME": str(tmp_path / "home"), "BRAIN_DIR": str(tmp_path / "brain"), "GBRAIN_SYNC_INTERVAL": "0"}

    def start():
        return subprocess.run(["sh", str(ENTRYPOINT)], env=env, capture_output=True, text=True, timeout=30)

    def calls():
        log = tmp_path / "log"
        return log.read_text().splitlines() if log.exists() else []
    return SimpleNamespace(start=start, calls=calls, home=tmp_path / "home", flag=lambda n: (tmp_path / n).touch(),
                           unflag=lambda n: (tmp_path / n).unlink())


def test_setup_reruns_until_every_step_went_through(box):
    box.flag("fail-set-path")
    assert box.start().returncode != 0
    assert not (box.home / ".markmik-setup-done").exists() and not (box.home / "app_token").exists()
    box.unflag("fail-set-path")
    r = box.start()
    assert r.returncode == 0, r.stderr
    assert sum(c.startswith("init") for c in box.calls()) == 1  # init ran once; the rest again
    assert sum(c.startswith("sources set-path") for c in box.calls()) == 2
    assert (box.home / ".markmik-setup-done").exists()
    token = box.home / "app_token"
    assert token.read_text() == TOKEN + "\n" and token.stat().st_mode & 0o777 == 0o600
    seen = len(box.calls())
    assert box.start().returncode == 0
    assert not [c for c in box.calls()[seen:] if c.split()[0] in ("init", "sources", "config", "auth")]


@pytest.mark.parametrize("flag", ["no-token", "fail-auth"])
def test_start_without_a_token_fails(box, flag):
    box.flag(flag)
    r = box.start()
    assert r.returncode != 0
    assert not (box.home / "app_token").exists() and not any(c.startswith("serve") for c in box.calls())
