#!/usr/bin/env bash
# Uses only the isolated local native staging profile. Never targets production.
set -euo pipefail
cd "$(dirname "$0")/.."
export ENV_FILE=.env.staging
export ENABLE_LEGACY_PASSWORD_AUTH=true
uv run python scripts/init-staging.py
uv run python scripts/native-services.py up staging
runner=(uv run python scripts/run-env.py)
"${runner[@]}" .venv/bin/alembic -c apps/api/alembic.ini upgrade head
uv run python scripts/staging-bootstrap.py
bash scripts/build-web.sh
staging_pid=
stop_staging() {
  if [[ -n "$staging_pid" ]]; then kill "$staging_pid" 2>/dev/null || true; wait "$staging_pid" || true; staging_pid=; fi
}
trap stop_staging EXIT
start_staging() {
  bash scripts/staging.sh > verification/native-staging.log 2>&1 & staging_pid=$!
  for attempt in $(seq 1 60); do
    if curl -ksSf https://localhost:8443/api/v1/health/ready >/dev/null 2>&1 && curl -ksSf https://localhost:8443/ >/dev/null 2>&1; then return; fi
    if ! kill -0 "$staging_pid" 2>/dev/null; then echo 'Staging exited; inspect verification/native-staging.log' >&2; exit 1; fi
    sleep 1
  done
  echo 'Staging did not become ready' >&2; exit 1
}
start_staging
ln -sfn ../../apps/web/node_modules tests/staging/node_modules
pnpm --dir apps/web exec playwright test --config ../../tests/staging/playwright.config.ts
bash scripts/backup.sh backups/staging
backup_file=$(python3 -c 'from pathlib import Path; print(sorted(Path("backups/staging").glob("*.dump"))[-1])')
"${runner[@]}" .venv/bin/python - <<'PY'
import psycopg
from pulsegrid.config import settings
url = settings().database_url.replace('postgresql+psycopg://', 'postgresql://', 1)
with psycopg.connect(url) as c:
    c.execute("INSERT INTO audit_events (id,user_id,kind,created_at) SELECT gen_random_uuid(),id,'final_restore_marker',now() FROM users LIMIT 1")
PY
stop_staging
bash scripts/restore.sh "$backup_file" --confirm-replace-database --writers-stopped
"${runner[@]}" .venv/bin/python scripts/check-restored-secrets.py
uv run python scripts/native-services.py restart staging
start_staging
"${runner[@]}" .venv/bin/python - <<'PY'
import psycopg
from pulsegrid.config import settings
url = settings().database_url.replace('postgresql+psycopg://', 'postgresql://', 1)
with psycopg.connect(url) as c:
    assert c.execute("SELECT count(*) FROM audit_events WHERE kind='final_restore_marker'").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM sessions WHERE revoked_at IS NULL").fetchone()[0] == 0
    assert c.execute("SELECT count(*) FROM check_results").fetchone()[0] > 0
    assert c.execute("SELECT count(*) FROM recovery_codes").fetchone()[0] == 0
print('Restored history survived a native PostgreSQL/Redis restart; one-time credentials invalidated')
PY
printf '\nNative local staging release verification completed.\n'
