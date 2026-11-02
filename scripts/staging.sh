#!/usr/bin/env bash
# Foreground native staging; build with scripts/build-web.sh first.
set -euo pipefail
cd "$(dirname "$0")/.."
export ENV_FILE=.env.staging
uv run python scripts/init-staging.py
uv run python scripts/native-services.py up staging
runner=(uv run python scripts/run-env.py)
"${runner[@]}" .venv/bin/alembic -c apps/api/alembic.ini upgrade head
mkdir -p verification .local-services/staging/caddy-data .local-services/staging/caddy-config
pids=()
cleanup() { for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done; wait || true; }
trap cleanup EXIT INT TERM
"${runner[@]}" .venv/bin/uvicorn pulsegrid.api.main:app --host 127.0.0.1 --port 8002 --no-proxy-headers > verification/staging-api.log 2>&1 & pids+=("$!")
"${runner[@]}" .venv/bin/python -m pulsegrid.worker.runner > verification/staging-worker.log 2>&1 & pids+=("$!")
HOSTNAME=127.0.0.1 PORT=3002 NODE_ENV=production node apps/web/.next/standalone/server.js > verification/staging-web.log 2>&1 & pids+=("$!")
XDG_DATA_HOME="$PWD/.local-services/staging/caddy-data" XDG_CONFIG_HOME="$PWD/.local-services/staging/caddy-config" caddy run --config infra/Caddyfile.staging > verification/staging-caddy.log 2>&1 & pids+=("$!")
echo 'Native staging: https://localhost:8443 — Ctrl-C stops application processes; database data is retained.'
while true; do
  for pid in "${pids[@]}"; do
    if ! kill -0 "$pid" 2>/dev/null; then echo 'A staging service exited; inspect verification/staging-*.log' >&2; exit 1; fi
  done
  sleep 1 & wait $!
done
