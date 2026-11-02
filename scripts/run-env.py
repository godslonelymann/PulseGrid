"""Run a command with ENV_FILE (default .env); existing environment values win."""

import os
import sys
from pathlib import Path

from dotenv import dotenv_values

root = Path(__file__).resolve().parents[1]
os.chdir(root)
env_file = Path(os.environ.get("ENV_FILE", ".env"))
if not env_file.is_file():
    raise SystemExit(f"Environment file does not exist: {env_file}")
for key, value in dotenv_values(env_file).items():
    if value is not None:
        os.environ.setdefault(key, value)
os.environ["PYTHONPATH"] = str(root / "apps/api") + os.pathsep + os.environ.get("PYTHONPATH", "")
if len(sys.argv) < 2:
    raise SystemExit("Usage: uv run python scripts/run-env.py COMMAND [ARGS...]")
os.execvpe(sys.argv[1], sys.argv[1:], os.environ)
