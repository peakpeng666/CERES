#!/usr/bin/env bash
set -eo pipefail
urp_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
operation="${1:-help}"
if (( $# )); then shift; fi
cd "$urp_root"
# Desktop launches do not load nvm; use its newest node when none is on PATH (lab operations only).
lab_node_path() {
  command -v node >/dev/null 2>&1 && return 0
  local node_bin
  node_bin="$(ls -d "$HOME"/.nvm/versions/node/*/bin 2>/dev/null | sort -V | tail -n 1 || true)"
  [[ -n "$node_bin" ]] && export PATH="$node_bin:$PATH"
  return 0
}
case "$operation" in
  reproduce) exec python3 tools/platform_deploy.py "$@" ;;
  build) exec bash setup/profile.sh frontend npm run build --prefix src/frontend -- "$@" ;;
  frontend) exec bash setup/profile.sh frontend bash src/start.sh frontend "$@" ;;
  backend) exec bash src/start.sh backend "$@" ;;
  research) exec bash setup/profile.sh research bash src/start.sh python src/research/scripts/urp.py "$@" ;;
  numpy) exec bash setup/profile.sh numpy src/frontend/.venv/bin/python "$@" ;;
  solver-check)
    exec bash setup/profile.sh research bash src/start.sh python setup/2026-10-15/deployment/benchmark.py solver --workers "${URP_WORKERS:-12}"
    ;;
  navigation)
    source /opt/ros/humble/setup.bash
    if [[ ${URP_BRIDGE_FIX:-1} == 1 ]]; then
      bridge_prefix="$(/usr/bin/python3 src/lab/ros/bridge_runtime.py)"
      export AMENT_PREFIX_PATH="$bridge_prefix:${AMENT_PREFIX_PATH:-/opt/ros/humble}"
    fi
    navigation_flags=(--ordered-bridge-stop)
    case "${URP_TF_FIX:-1}" in
      1) navigation_flags+=(--tf-candidate) ;;
      0) ;;
      *) printf 'URP_TF_FIX must be 0 or 1.\n' >&2; exit 2 ;;
    esac
    case "${URP_GUI_FIX:-1}" in
      1) navigation_flags+=(--pin-gui-plugins) ;;
      0) ;;
      *) printf 'URP_GUI_FIX must be 0 or 1.\n' >&2; exit 2 ;;
    esac
    # Unique labels preserve all previous runs; Ctrl+C is handled by the supervisor.
    exec bash setup/profile.sh simulation /usr/bin/python3 setup/2026-10-15/deployment/nav-smoke.py \
      --label "run-$(date -u +%Y%m%d-%H%M%S)-$$" "${navigation_flags[@]}" "$@"
    ;;
  lab|lab-code)
    # Always build: source deletion, lock/config/tool/public changes and preserved
    # mtimes must never silently reuse stale dist. Fail closed before the gateway.
    lab_node_path
    printf '构建当前前端（不复用旧构建）…\n'
    if ! bash setup/profile.sh frontend npm run build --prefix src/frontend; then
      printf '前端构建失败；停止启动，避免把旧页面作为当前实现。\n' >&2
      exit 1
    fi
    if [[ $operation == lab ]]; then
      overlay_dir=setup/2026-10-15/deployment/runtime
      if [[ ! -f $overlay_dir/tf-repro/liburp_tf_buffer.so || ! -f $overlay_dir/liburp_gui_lifetime.so ]]; then
        printf '重建仿真修补库（TF2 / GUI，与验收二进制逐字节一致）…\n'
        bash src/lab/ros/native/rebuild-ros-overlays.sh || printf '仿真修补库重建失败；规划功能不受影响，仿真启动会提示原因。\n' >&2
      fi
      if [[ ${URP_BRIDGE_FIX:-1} == 1 ]]; then
        /usr/bin/python3 src/lab/ros/bridge_runtime.py >/dev/null || printf '桥接修复验证失败；规划仍可使用，仿真将报告具体原因。\n' >&2
      fi
    fi
    cd src/lab
    exec .venv/bin/python -m urp_lab serve "$@"
    ;;
  lab-setup)
    lab_node_path
    # The archive / CVRP jobs service uses its own locked root environment.
    UV_CACHE_DIR="$urp_root/setup/2026-10-15/cache/uv" uv sync --locked --extra solvers
    (cd src/lab && UV_CACHE_DIR="$urp_root/setup/2026-10-15/cache/uv" uv sync --locked --inexact)
    # Reuse the optional, locally built wheel only after checking its pinned bytes.
    if [[ -f src/lab/vendor/wheels/fields2cover-2.1.0-cp312-cp312-linux_x86_64.whl ]]; then
      src/lab/.venv/bin/python - <<'PY'
import hashlib,json,pathlib,subprocess
lab=pathlib.Path('src/lab');m=json.loads((lab/'vendor/fields2cover-manifest.json').read_text());p=lab/m['wheel']
if hashlib.sha256(p.read_bytes()).hexdigest()!=m['wheel_sha256']:raise SystemExit('Fields2Cover wheel 指纹不匹配，拒绝安装')
subprocess.run(['uv','pip','install','--no-deps','--python',str(lab/'.venv/bin/python'),str(p)],check=True)
PY
    fi
    if [[ ! -d src/frontend/node_modules ]]; then (cd src/frontend && npm ci); fi
    bash src/lab/ros/native/rebuild-ros-overlays.sh
    /usr/bin/python3 src/lab/ros/bridge_runtime.py
    exec bash setup/profile.sh frontend npm run build --prefix src/frontend
    ;;
  lab-stop)
    # Same loopback target as before. URP_PORT and URP_ADMIN_TOKEN are optional.
    port="${URP_PORT:-4100}"
    token_header=()
    if [[ -n "${URP_ADMIN_TOKEN:-}" ]]; then
      token_header=(-H "X-URP-Admin-Token: ${URP_ADMIN_TOKEN}")
    fi
    exec curl --fail-with-body -sS -X POST -H 'Content-Type: application/json' "${token_header[@]}" \
      -d '{}' "http://127.0.0.1:${port}/lab/api/shutdown"
    ;;
  *) printf 'Usage: bash setup/run.sh reproduce|lab|lab-code|lab-stop|lab-setup|build|frontend|backend|research|numpy|solver-check|navigation [ARGS...]\n' ;;
esac
