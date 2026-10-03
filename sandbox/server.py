"""The arvutus container's server (docs/ARVUTUS-PLAN.md): one JSON request per connection on a Unix
socket, run as a fresh `kernel.py` process with resource limits, one at a time.

The container has no network, no secrets and a read-only root; this file adds what the container
cannot: a time limit per run, rlimits, and a clean slate between runs. The host's
kernel.yama.ptrace_scope is 0, so a process of the same user could read another's memory or open
its pipes: the server makes itself non-dumpable, runs never overlap, and every process but the
server is killed and /tmp emptied before and after each run, so nothing a note's code leaves behind
sees the next note.
"""

import ctypes
import json
import os
import resource
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time

SOCKET = os.environ.get("ARVUTUS_SOCKET", "/run/arvutus/arvutus.sock")
KERNEL = os.path.join(os.path.dirname(os.path.abspath(__file__)), "kernel.py")
REQUEST_CAP = 2 * 1024 * 1024
ANSWER_CAP = 4 * 1024 * 1024
MAX_SECONDS = 30
MEMORY = 512 * 1024 * 1024
PR_SET_DUMPABLE = 4

lock = threading.Lock()  # one run at a time: see the module docstring


def not_dumpable():
    try:
        ctypes.CDLL(None, use_errno=True).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0)
    except (OSError, AttributeError):
        pass


def clean_slate(scratch_root):
    """Kill every process except this one and empty the scratch space. The killing happens only as
    the container's PID 1: anywhere else it would take the whole user session with it."""
    me = os.getpid()
    for name in os.listdir("/proc") if me == 1 else ():
        if name.isdigit() and int(name) not in (me, 1):
            try:
                os.kill(int(name), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    # As the container's PID 1 the server inherits every orphan: reap them, or the zombies fill
    # the pids limit and no run can start.
    deadline = time.monotonic() + (2 if me == 1 else 0)
    while time.monotonic() < deadline:
        try:
            pid, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if pid == 0:
            time.sleep(0.01)
    for name in os.listdir(scratch_root):
        path = os.path.join(scratch_root, name)
        try:
            shutil.rmtree(path) if os.path.isdir(path) and not os.path.islink(path) else os.remove(path)
        except OSError:
            pass


def limits(seconds):
    def apply():
        os.setsid()
        resource.setrlimit(resource.RLIMIT_CPU, (seconds, seconds + 1))
        resource.setrlimit(resource.RLIMIT_AS, (MEMORY, MEMORY))
        resource.setrlimit(resource.RLIMIT_FSIZE, (8 * 1024 * 1024, 8 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    return apply


def run(request, scratch_root):
    seconds = max(1, min(int(request.pop("timeout", 5) or 5), MAX_SECONDS))
    started = time.monotonic()
    with lock:
        clean_slate(scratch_root)
        work = tempfile.mkdtemp(dir=scratch_root)
        env = {"PATH": "/usr/local/bin:/usr/bin:/bin", "HOME": work, "TMPDIR": work, "LANG": "C.UTF-8",
               "PYTHONDONTWRITEBYTECODE": "1", "OPENBLAS_NUM_THREADS": "1", "MPLCONFIGDIR": work}
        try:
            proc = subprocess.Popen([sys.executable, "-I", KERNEL], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.DEVNULL, cwd=work, env=env, preexec_fn=limits(seconds),
                                    close_fds=True)
            # Wait for the kernel, not for the end of its output: a process it left behind holds
            # the pipe open until clean_slate kills it.
            chunks = []
            reader = threading.Thread(target=lambda: chunks.extend(iter(lambda: proc.stdout.read(65536), b"")),
                                      daemon=True)
            reader.start()
            try:
                proc.stdin.write(json.dumps(request).encode() + b"\n")
                proc.stdin.close()
            except BrokenPipeError:
                pass
            try:
                proc.wait(timeout=seconds + 2)
                timed_out = False
            except subprocess.TimeoutExpired:
                timed_out = True
        finally:
            clean_slate(scratch_root)
        reader.join(2)
        out = b"".join(chunks)
        if timed_out:
            return {"error": f"Arvutus ei lõppenud {seconds} sekundiga."}
    seconds_used = round(time.monotonic() - started, 2)
    if len(out) > ANSWER_CAP:
        return {"error": "Arvutuse väljund on liiga suur."}
    last = out.decode("utf-8", "replace").rstrip("\n").rsplit("\n", 1)[-1]
    try:
        answer = json.loads(last)
    except ValueError:
        # Killed by an rlimit (memory, CPU) or the code ended the process itself.
        reason = "mälu või protsessoriaja piir" if proc.returncode and proc.returncode < 0 else "protsess lõppes"
        return {"error": f"Arvutus katkes ({reason})."}
    if not isinstance(answer, dict):
        return {"error": "Arvutus andis vigase vastuse."}
    answer["seconds"] = seconds_used
    return answer


def handle(conn, scratch_root):
    with conn:
        conn.settimeout(10)
        data = b""
        while not data.endswith(b"\n"):
            chunk = conn.recv(65536)
            if not chunk:
                return
            data += chunk
            if len(data) > REQUEST_CAP:
                conn.sendall(json.dumps({"error": "Märge on arvutamiseks liiga suur."}).encode() + b"\n")
                return
        try:
            request = json.loads(data)
            answer = run(request, scratch_root) if isinstance(request, dict) else {"error": "Vigane päring."}
        except ValueError:
            answer = {"error": "Vigane päring."}
        conn.settimeout(None)
        conn.sendall(json.dumps(answer, ensure_ascii=False).encode() + b"\n")


def main():
    not_dumpable()
    scratch_root = os.environ.get("ARVUTUS_SCRATCH", "/tmp")
    try:
        os.unlink(SOCKET)
    except FileNotFoundError:
        pass
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(SOCKET)
    os.chmod(SOCKET, 0o666)  # the app runs as another user; the socket's directory is only shared with it
    server.listen(8)
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    while True:
        conn, _ = server.accept()
        threading.Thread(target=handle, args=(conn, scratch_root), daemon=True).start()


if __name__ == "__main__":
    main()
