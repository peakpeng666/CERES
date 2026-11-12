#!/usr/bin/env bash
# Uses the user's configured PATH adapter (Ghostty), including cwd/title handling.
set -euo pipefail
ceres_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
terminal_exe="$(command -v x-terminal-emulator)"
exec "$terminal_exe" --wait --title="CERES — ${1:-help}" --working-directory="$ceres_root" \
  -- bash "$ceres_root/setup/run.sh" "$@"
