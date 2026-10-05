"""The sandbox's kernel as a local process, for the arvuta tests (a module name of its own: every test
folder has a conftest.py, so `from conftest import ...` can find another folder's)."""

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "sandbox"))
from local import run_local  # noqa: E402


def run_kernel(request, seconds=10):
    return run_local(request, seconds)
