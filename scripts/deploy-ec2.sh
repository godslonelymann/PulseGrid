#!/usr/bin/env bash
# Update the existing shared EC2 installation after pulling the repository.
set -euo pipefail

if [[ "$EUID" -ne 0 ]]; then
  echo 'Run with sudo: sudo bash scripts/deploy-ec2.sh' >&2
  exit 1
fi
repo_root=$(cd "$(dirname "$0")/.." && pwd)
app_root=/opt/pulsegrid
runtime_root=/opt/pulsegrid-runtime
env_file=/etc/pulsegrid/pulsegrid.env
test -f "$env_file"
test -x "$app_root/.venv/bin/python"
test -d "$app_root/apps/web/.next"
test -d "$app_root/apps/web/node_modules"
id pulsegrid >/dev/null
for tool in rsync flock systemd-run uv pnpm; do command -v "$tool" >/dev/null; done
exec 9>/run/lock/pulsegrid-deploy.lock
flock -n 9 || { echo 'Another PulseGrid deployment is running' >&2; exit 1; }
source_commit=$(git -c safe.directory="$repo_root" -C "$repo_root" rev-parse HEAD)

release=$(date -u +%Y%m%dT%H%M%SZ)
backup_dir=/var/backups/pulsegrid-deploy/releases/$release
build_dir=/opt/pulsegrid-web-build-$release
install -d -m 0700 "$backup_dir"
install -d -o pulsegrid -g pulsegrid "$build_dir"
trap 'echo "Deployment failed. Review the error above; backups are in $backup_dir. No database rollback was attempted." >&2' ERR

# Build away from the running frontend, within the shared host's resource budget.
rsync -a --exclude=node_modules --exclude='.next*' "$repo_root/apps/web/" "$build_dir/"
chown -R pulsegrid:pulsegrid "$build_dir"
systemd-run --unit="pulsegrid-release-$release" --wait --pipe --collect \
  --uid=pulsegrid --gid=pulsegrid --property="WorkingDirectory=$build_dir" \
  --property=MemoryMax=1400M --property=CPUQuota=100% --property=Nice=10 \
  --setenv=NODE_OPTIONS=--max-old-space-size=768 --setenv=NEXT_TELEMETRY_DISABLED=1 \
  --setenv=PATH=/usr/local/bin:/usr/bin:/bin \
  /bin/bash -e -c 'pnpm install --frozen-lockfile
pnpm build
mkdir -p .next/standalone/.next
cp -a .next/static .next/standalone/.next/
if [[ -d public ]]; then cp -a public .next/standalone/; fi'

cd "$app_root"
ENV_FILE="$env_file" .venv/bin/python scripts/run-env.py .venv/bin/python scripts/database.py backup "$backup_dir/database"
rsync -a --exclude=.venv --exclude=node_modules --exclude='.next*' --exclude=.env --exclude='.env.*' \
  --exclude=verification --exclude=backups --exclude=.local-services "$app_root/" "$backup_dir/source/"

# Preserve the existing domain routing, API port 8100, service limits and credentials.
systemctl stop pulsegrid-api pulsegrid-worker pulsegrid-web
rsync -a --exclude=.git --exclude=.venv --exclude=node_modules --exclude='.next*' \
  --exclude=.env --exclude='.env.*' --exclude=verification --exclude=backups \
  --exclude=.local-services "$repo_root/" "$app_root/"
chown -R pulsegrid:pulsegrid "$app_root"
sudo -u pulsegrid -H env UV_PYTHON_INSTALL_DIR="$runtime_root/python" UV_PYTHON_DOWNLOADS=never \
  /usr/local/bin/uv sync --frozen --python 3.13
sudo -u pulsegrid -H env ENV_FILE="$env_file" .venv/bin/python scripts/run-env.py \
  .venv/bin/alembic -c apps/api/alembic.ini upgrade head
mv apps/web/.next "$backup_dir/previous-web-build"
mv apps/web/node_modules "$backup_dir/previous-web-dependencies"
mv "$build_dir/.next" apps/web/.next
mv "$build_dir/node_modules" apps/web/node_modules
systemctl start pulsegrid-api pulsegrid-worker pulsegrid-web

ready=false
for attempt in $(seq 1 20); do
  if curl -fsS --max-time 5 http://127.0.0.1:8100/api/v1/health/ready >/dev/null \
      && curl -fsS --max-time 5 http://127.0.0.1:3000/ >/dev/null; then
    ready=true
    break
  fi
  sleep 2
done
test "$ready" = true
sudo -u pulsegrid -H env ENV_FILE="$env_file" .venv/bin/python scripts/run-env.py \
  .venv/bin/python -m pulsegrid.worker.healthcheck
printf '%s\n' "$source_commit" > "$backup_dir/source-commit"
echo 'PulseGrid updated. Caddy and the other applications were not restarted.'
echo "Release backup: $backup_dir"
