#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --frozen
uv run python scripts/init-env.py
uv run python scripts/native-services.py up dev
uv run alembic -c apps/api/alembic.ini upgrade head
pnpm --dir apps/web install --frozen-lockfile
export PYTHONPATH=apps/api
pids=()
cleanup() { for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done; wait || true; }
trap cleanup EXIT INT TERM
uv run uvicorn pulsegrid.api.main:app --host 127.0.0.1 --port 8000 --no-proxy-headers & pids+=("$!")
uv run python -m pulsegrid.worker.runner & pids+=("$!")
pnpm --dir apps/web dev & pids+=("$!")
echo 'PulseGrid: http://localhost:3000 — Ctrl-C stops application processes; local database data is retained.'
wait
