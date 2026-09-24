import os
import subprocess
import pytest

# The laptop's git must not pick up the host's config (signing, hooks).
GIT_ENV = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
           "GIT_AUTHOR_NAME": "laptop", "GIT_AUTHOR_EMAIL": "l@l", "GIT_COMMITTER_NAME": "laptop",
           "GIT_COMMITTER_EMAIL": "l@l"}


class Laptop:
    """A second clone of the brain repo: the user's laptop."""

    def __init__(self, remote, path):
        self.path = path
        subprocess.run(["git", "clone", "-q", str(remote), str(path)], check=True, env=GIT_ENV)

    def git(self, *args):
        return subprocess.run(["git", "-C", str(self.path), *args], check=True, capture_output=True,
                              text=True, env=GIT_ENV).stdout

    def pull(self):
        self.git("pull", "-q", "--rebase")

    def read(self, rel):
        return (self.path / rel).read_text()

    def exists(self, rel):
        return (self.path / rel).exists()

    def write(self, rel, text):
        (self.path / rel).parent.mkdir(parents=True, exist_ok=True)
        (self.path / rel).write_text(text)

    def push(self, message="laptop"):
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)
        self.git("push", "-q")


@pytest.fixture
def brain(conn, brain_remote):
    """The app pointed at the bare test remote (no token needed for a local path)."""
    conn.execute("INSERT INTO settings (key, value) VALUES ('brain_remote', ?)", (str(brain_remote),))
    return brain_remote


@pytest.fixture
def laptop(brain, tmp_path):
    return Laptop(brain, tmp_path / "laptop")
