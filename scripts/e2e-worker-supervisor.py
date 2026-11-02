"""Verification-only crash control for the real worker process."""

import json
import os
import signal
import subprocess
import time
from pathlib import Path

stopping = False


def stop(*_):
    global stopping
    stopping = True


for sig in (signal.SIGTERM, signal.SIGINT):
    signal.signal(sig, stop)
marker = Path("verification/e2e-worker.restart")
marker.unlink(missing_ok=True)
state = Path("verification/e2e-worker-state.json")
restarts = 0


def spawn():
    child = subprocess.Popen([".venv/bin/python", "-m", "pulsegrid.worker.runner"], env=os.environ.copy())
    tmp = state.with_suffix(".tmp")
    tmp.write_text(json.dumps({"pid": child.pid, "restarts": restarts}))
    tmp.replace(state)
    return child


worker = spawn()
try:
    while not stopping:
        if marker.exists():
            marker.unlink()
            worker.kill()
            worker.wait()
            restarts += 1
            worker = spawn()
        if worker.poll() is not None:
            raise SystemExit("Production worker exited unexpectedly")
        time.sleep(0.2)
finally:
    if worker.poll() is None:
        worker.terminate()
        try:
            worker.wait(timeout=30)
        except subprocess.TimeoutExpired:
            worker.kill()
            worker.wait()
