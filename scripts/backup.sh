#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
exec uv run python scripts/run-env.py .venv/bin/python scripts/database.py backup "$@"
