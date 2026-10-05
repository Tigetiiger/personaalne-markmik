"""The sandbox's run without the container (tests and the browser test harness): a temporary folder
laid out as server.py lays it out, the kernel started in it. No limits and no isolation: never for
code from notes in production."""

import json
import os
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from server import write_files  # noqa: E402


def run_local(request, seconds=10):
    request = dict(request)
    with tempfile.TemporaryDirectory() as work:
        run_dir = os.path.join(work, "kaust")
        os.makedirs(os.path.join(run_dir, "valjund"))
        problem = write_files(run_dir, request.pop("files", None) or [])
        if problem:
            return {"error": problem}
        request.pop("timeout", None)
        out = subprocess.run([sys.executable, os.path.join(HERE, "kernel.py")], input=json.dumps(request),
                             capture_output=True, text=True, timeout=seconds + 20, cwd=run_dir,
                             env={**os.environ, "MPLBACKEND": "Agg"})
    return json.loads(out.stdout.rstrip("\n").rsplit("\n", 1)[-1])
