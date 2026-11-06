#!/usr/bin/env bash
set -euo pipefail
URP_SRC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
URP_DEFAULT_PYTHON=python3
if [[ -x "$URP_SRC/../.venv/bin/python" ]]; then URP_DEFAULT_PYTHON="$URP_SRC/../.venv/bin/python"; fi
URP_RUNTIME_PYTHON="${URP_PYTHON:-$URP_DEFAULT_PYTHON}"
if [[ "$URP_RUNTIME_PYTHON" == */* && "$URP_RUNTIME_PYTHON" != /* ]]; then
  URP_RUNTIME_PYTHON="$(pwd -P)/$URP_RUNTIME_PYTHON"
fi
URP_DEFAULT_DB="$URP_SRC/../.local-state/research.sqlite3"
if [[ -n "${URP_DATA_HOME:-}" ]]; then URP_DEFAULT_DB="${URP_DATA_HOME%/}/archive/research.sqlite3"; fi
URP_DB="${URP_API_DB:-$URP_DEFAULT_DB}"
if [[ "$URP_DB" != /* ]]; then URP_DB="$(pwd -P)/$URP_DB"; fi
prepare_default_database() {
  if [[ -z "${URP_API_DB:-}" ]]; then
    "$URP_RUNTIME_PYTHON" "$URP_SRC/../tools/runtime_db.py" \
      --source "$URP_SRC/../data/backend/research.sqlite3" --destination "$URP_DB"
  fi
}
URP_COMMAND="${1:-help}"
if (( $# )); then shift; fi
case "$URP_COMMAND" in
  python-path)
    # Shared by service diagnostics; resolve exactly as a launch from this cwd.
    command -v -- "$URP_RUNTIME_PYTHON" || { printf 'Python interpreter unavailable: %s\n' "$URP_RUNTIME_PYTHON" >&2; exit 1; }
    ;;
  python) exec "$URP_RUNTIME_PYTHON" "$@" ;;
  backend|后端)
    prepare_default_database
    cd "$URP_SRC/backend"
    # Import data/packages into the runtime database before serving.
    # An unchanged package reuses its revision. The frozen data/backend snapshot is not written.
    "$URP_RUNTIME_PYTHON" -m backend --db "$URP_DB" bootstrap --package-root "$URP_SRC/../data/packages"
    exec "$URP_RUNTIME_PYTHON" -m backend --db "$URP_DB" serve --import-root "$URP_SRC/../data/packages" --port "${URP_API_PORT:-4180}" "$@"
    ;;
  bootstrap|导入)
    prepare_default_database
    cd "$URP_SRC/backend"
    exec "$URP_RUNTIME_PYTHON" -m backend --db "$URP_DB" bootstrap --package-root "$URP_SRC/../data/packages" "$@"
    ;;
  frontend|前端)
    exec bash "$URP_SRC/frontend/scripts/console.sh" serve "$@"
    ;;
  dev|开发)
    exec bash "$URP_SRC/frontend/scripts/console.sh" dev "$@"
    ;;
  check|检查)
    exec "$URP_RUNTIME_PYTHON" "$URP_SRC/../tools/check_project.py" "$@"
    ;;
  build|构建)
    exec bash "$URP_SRC/frontend/scripts/console.sh" build "$@"
    ;;
  help|-h|--help) printf 'Usage: bash src/start.sh bootstrap | backend | dev | frontend | build | check\n';;
  *) printf 'Unknown command: %s\n' "$URP_COMMAND" >&2; exit 2;;
esac
