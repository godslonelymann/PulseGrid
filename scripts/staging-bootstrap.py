"""Create an isolated local-staging owner using the real operator command."""

import os
import secrets
import subprocess
from pathlib import Path

p = Path(".env.staging")
if not p.exists():
    raise SystemExit("Create staging environment first; see docs/VERIFICATION.md")
values = dict(line.split("=", 1) for line in p.read_text().splitlines() if "=" in line)
password = values.get("STAGING_TEST_PASSWORD")
if not password:
    password = secrets.token_urlsafe(32)
    with p.open("a") as f:
        f.write("STAGING_TEST_PASSWORD=" + password + "\n")
command = [
    ".venv/bin/python",
    "scripts/run-env.py",
    ".venv/bin/python",
    "-m",
    "pulsegrid.manage",
    "bootstrap",
    "staging@example.com",
]
os.environ["ENV_FILE"] = ".env.staging"

r = subprocess.run(command, input=password + "\n" + password + "\n", text=True, capture_output=True)
if r.returncode:
    if "Owner exists" in r.stderr:
        command[-2] = "recover"
        recovered = subprocess.run(command, input=password + "\n" + password + "\n", text=True, capture_output=True)
        if recovered.returncode:
            raise SystemExit(recovered.stderr.replace(password, "[redacted]"))
        print("Staging test owner reset through operator recovery")
    else:
        raise SystemExit(r.stderr.replace(password, "[redacted]"))
else:
    print("Staging owner bootstrapped through real operator command")
