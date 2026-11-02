#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
uv sync --frozen
cleanup() { uv run python scripts/native-services.py down feasibility; }
trap cleanup EXIT
uv run python scripts/native-services.py up feasibility
uv run --frozen python -m feasibility.run
