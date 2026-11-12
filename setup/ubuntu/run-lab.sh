#!/usr/bin/env bash
# Start the workbench. WSL2 binds all interfaces so the Windows browser can use
# localhost forwarding. Native Ubuntu keeps the laptop default, 127.0.0.1.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$root"
# shellcheck source=env.sh
source "$root/setup/ubuntu/env.sh"
if [[ ! -x .venv/bin/python || ! -x src/lab/.venv/bin/python || ! -f src/frontend/dist/index.html ]]; then
  printf '工作台依赖或前端尚未准备好；启动命令不会隐式运行 sudo 安装。\n' >&2
  printf '完整工作台与 CVRP 基线：bash setup/ubuntu/setup.sh --components full\n' >&2
  printf '无 ROS 的数值/规划环境：bash setup/ubuntu/setup.sh --components research\n' >&2
  exit 1
fi
if [[ ! -r /opt/ros/humble/setup.bash ]]; then
  printf '未安装 ROS 2 Humble：仅启动规划/数值工作台，真实仿真不可用。\n' >&2
fi
wsl=0
if [[ -n "${WSL_DISTRO_NAME:-}" ]] || grep -qi microsoft /proc/version 2>/dev/null; then
  wsl=1
fi
if [[ -f src/lab/runtime/native/bridge/profile.json ]]; then
  export CERES_SIM_PROFILE=image
fi
# Gazebo server mode still opens GLX for sensors. A desktop or WSLg display is used
# as-is. Headless machines get a private Xvfb screen.
if [[ -z "${DISPLAY:-}" ]] && command -v Xvfb >/dev/null 2>&1; then
  export DISPLAY=:99
  if [[ ! -e /tmp/.X99-lock ]]; then
    Xvfb :99 -screen 0 1280x1024x24 -nolisten tcp >>/tmp/xvfb.log 2>&1 &
  fi
fi
if [[ "$wsl" -eq 1 ]]; then
  export CERES_BIND_HOST=0.0.0.0
  export CERES_FOXGLOVE_BIND=0.0.0.0
  export CERES_BROWSER_HOST=127.0.0.1
  export LIBGL_ALWAYS_SOFTWARE="${LIBGL_ALWAYS_SOFTWARE:-1}"
else
  export CERES_BIND_HOST="${CERES_BIND_HOST:-127.0.0.1}"
  export CERES_FOXGLOVE_BIND="${CERES_FOXGLOVE_BIND:-127.0.0.1}"
  export CERES_BROWSER_HOST="${CERES_BROWSER_HOST:-127.0.0.1}"
fi
exec bash setup/run.sh lab "$@"

