#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --frozen
uv run python scripts/init-env.py
uv run python scripts/native-services.py up test
DATABASE_URL=postgresql+psycopg://pulsegrid:test-only@localhost:55433/pulsegrid_test uv run alembic -c apps/api/alembic.ini upgrade head
uv run pytest -q "$@"
