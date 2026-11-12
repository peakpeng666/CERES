#!/usr/bin/env bash
set -euo pipefail
CERES_SRC="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
CERES_DEFAULT_PYTHON=python3
if [[ -x "$CERES_SRC/../.venv/bin/python" ]]; then CERES_DEFAULT_PYTHON="$CERES_SRC/../.venv/bin/python"; fi
CERES_RUNTIME_PYTHON="${CERES_PYTHON:-$CERES_DEFAULT_PYTHON}"
if [[ "$CERES_RUNTIME_PYTHON" == */* && "$CERES_RUNTIME_PYTHON" != /* ]]; then
  CERES_RUNTIME_PYTHON="$(pwd -P)/$CERES_RUNTIME_PYTHON"
fi
CERES_DEFAULT_DB="$CERES_SRC/../.local-state/research.sqlite3"
if [[ -n "${CERES_DATA_HOME:-}" ]]; then CERES_DEFAULT_DB="${CERES_DATA_HOME%/}/archive/research.sqlite3"; fi
CERES_DB="${CERES_API_DB:-$CERES_DEFAULT_DB}"
if [[ "$CERES_DB" != /* ]]; then CERES_DB="$(pwd -P)/$CERES_DB"; fi
prepare_default_database() {
  if [[ -z "${CERES_API_DB:-}" ]]; then
    "$CERES_RUNTIME_PYTHON" "$CERES_SRC/../tools/runtime_db.py" \
      --source "$CERES_SRC/../data/backend/research.sqlite3" --destination "$CERES_DB"
  fi
}
CERES_COMMAND="${1:-help}"
if (( $# )); then shift; fi
case "$CERES_COMMAND" in
  python-path)
    # Shared by service diagnostics; resolve exactly as a launch from this cwd.
    command -v -- "$CERES_RUNTIME_PYTHON" || { printf 'Python interpreter unavailable: %s\n' "$CERES_RUNTIME_PYTHON" >&2; exit 1; }
    ;;
  python) exec "$CERES_RUNTIME_PYTHON" "$@" ;;
  backend|后端)
    prepare_default_database
    cd "$CERES_SRC/backend"
    # Import data/packages into the runtime database before serving.
    # An unchanged package reuses its revision. The frozen data/backend snapshot is not written.
    "$CERES_RUNTIME_PYTHON" -m backend --db "$CERES_DB" bootstrap --package-root "$CERES_SRC/../data/packages"
    exec "$CERES_RUNTIME_PYTHON" -m backend --db "$CERES_DB" serve --import-root "$CERES_SRC/../data/packages" --port "${CERES_API_PORT:-4180}" "$@"
    ;;
  bootstrap|导入)
    prepare_default_database
    cd "$CERES_SRC/backend"
    exec "$CERES_RUNTIME_PYTHON" -m backend --db "$CERES_DB" bootstrap --package-root "$CERES_SRC/../data/packages" "$@"
    ;;
  frontend|前端)
    exec bash "$CERES_SRC/frontend/scripts/console.sh" serve "$@"
    ;;
  dev|开发)
    exec bash "$CERES_SRC/frontend/scripts/console.sh" dev "$@"
    ;;
  check|检查)
    exec "$CERES_RUNTIME_PYTHON" "$CERES_SRC/../tools/check_project.py" "$@"
    ;;
  build|构建)
    exec bash "$CERES_SRC/frontend/scripts/console.sh" build "$@"
    ;;
  help|-h|--help) printf 'Usage: bash src/start.sh bootstrap | backend | dev | frontend | build | check\n';;
  *) printf 'Unknown command: %s\n' "$CERES_COMMAND" >&2; exit 2;;
esac
