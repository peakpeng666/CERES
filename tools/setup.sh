#!/usr/bin/env bash
set -euo pipefail
CERES_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$CERES_ROOT"
command -v uv >/dev/null || { printf 'Install uv, then run bash tools/setup.sh again.\n' >&2; exit 1; }
command -v npm >/dev/null || { printf 'Node.js >=22.12 and npm are required.\n' >&2; exit 1; }
uv sync --locked "$@"
npm ci --prefix src/frontend --engine-strict --no-audit --no-fund
printf 'Ready. Run bash src/start.sh backend and bash src/start.sh dev in separate terminals.\n'
