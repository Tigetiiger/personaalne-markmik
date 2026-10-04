"""The sandbox's kernel as a local process, for the arvuta tests (a module name of its own: every test
folder has a conftest.py, so `from conftest import ...` can find another folder's)."""

import json
import pathlib
import subprocess
import sys

KERNEL = pathlib.Path(__file__).resolve().parents[2] / "sandbox" / "kernel.py"


def run_kernel(request, seconds=10):
    out = subprocess.run([sys.executable, str(KERNEL)], input=json.dumps(request), capture_output=True, text=True,
                         timeout=seconds + 20)
    return json.loads(out.stdout.rstrip("\n").rsplit("\n", 1)[-1])
