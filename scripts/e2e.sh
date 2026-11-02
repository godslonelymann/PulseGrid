#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p verification
uv run python scripts/native-services.py up test
ln -sfn ../../apps/web/node_modules tests/e2e/node_modules
export PYTHONPATH=apps/api
export DATABASE_URL=postgresql+psycopg://pulsegrid:test-only@localhost:55433/pulsegrid_test
export REDIS_URL=redis://localhost:56380/0
export PUBLIC_ORIGIN=http://localhost:3001
export PRIVATE_TARGET_ALLOWLIST=127.0.0.1:18080
export ACCESS_SECONDS=8
# Browser fixtures must not use the operator's configured mail provider.
export SMTP_HOST= SMTP_USER= SMTP_PASSWORD= SMTP_FROM=
uv run alembic -c apps/api/alembic.ini upgrade head
uv run python scripts/e2e-seed.py
pids=()
cleanup() { for pid in "${pids[@]}"; do kill "$pid" 2>/dev/null || true; done; wait || true; }
trap cleanup EXIT INT TERM
uv run uvicorn pulsegrid.api.main:app --host 127.0.0.1 --port 8001 --no-proxy-headers > verification/e2e-api.log 2>&1 & pids+=("$!")
uv run uvicorn --app-dir scripts e2e-fixture:app --host 127.0.0.1 --port 18080 > verification/e2e-fixture.log 2>&1 & pids+=("$!")
uv run python scripts/e2e-worker-supervisor.py > verification/e2e-worker.log 2>&1 & pids+=("$!")
NEXT_DIST_DIR=.next-e2e API_INTERNAL_URL=http://127.0.0.1:8001 pnpm --dir apps/web dev --port 3001 > verification/e2e-web.log 2>&1 & pids+=("$!")
ready=false
for attempt in $(seq 1 60); do
  if curl -fsS http://localhost:3001 >/dev/null 2>&1 && curl -fsS http://127.0.0.1:8001/api/v1/health/ready >/dev/null 2>&1; then ready=true; break; fi
  for pid in "${pids[@]}"; do if ! kill -0 "$pid" 2>/dev/null; then echo 'A browser-test service exited; inspect verification/e2e-*.log' >&2; exit 1; fi; done
  sleep 1
done
if [[ "$ready" != true ]]; then echo 'Browser-test services did not become ready' >&2; exit 1; fi
pnpm --dir apps/web exec playwright test --config ../../tests/e2e/playwright.config.ts "$@"
