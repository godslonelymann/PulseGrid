#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../apps/web"
pnpm install --frozen-lockfile
pnpm build
# The standalone server needs static assets beside its server.js.
mkdir -p .next/standalone/.next
cp -R .next/static .next/standalone/.next/
if [[ -d public ]]; then cp -R public .next/standalone/; fi
