#!/usr/bin/env bash
# Ubuntu 22.04 setup for WSL2 and for a native machine. Installs the pinned
# Python/Node workbench, selected simulation and source-built CVRP components.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
cd "$root"
# shellcheck source=env.sh
source "$root/setup/ubuntu/env.sh"

components="${CERES_COMPONENTS:-full}"
f2c_mode=""
usage() {
  cat <<'EOF'
Usage: bash setup/ubuntu/setup.sh [--components workbench|research|full] [--fields2cover auto|required|skip]
  workbench: locked Python/Node workbench + ROS 2 Humble/Gazebo Fortress.
  research:  locked Python/Node workbench + source-built AILS/SISR, without ROS.
  full:      both sets + required pinned Fields2Cover binding (default).
  auto:      use a verified local wheel if present; do not download (default except full).
  required:  obtain the pinned Release wheel and verify its dynamic load.
  skip:      do not install Fields2Cover; allowed only for non-full components.
Full means the workbench and CVRP baselines, NOT all historical NAS environments.
Fuel assets are a separate explicit step (ceres fuel); historical snapshots are not installed.
EOF
}
while (( $# )); do
  case "$1" in
    --components|--fields2cover)
      if (( $# < 2 )); then usage >&2; exit 2; fi
      if [[ "$1" == --components ]]; then components="$2"; else f2c_mode="$2"; fi
      shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown setup option: $1" >&2; usage >&2; exit 2 ;;
  esac
done
case "$components" in workbench|research|full) ;; *) usage >&2; exit 2 ;; esac
if [[ -z "$f2c_mode" ]]; then
  if [[ "$components" == full ]]; then f2c_mode=required; else f2c_mode=auto; fi
fi
case "$f2c_mode" in auto|required|skip) ;; *) usage >&2; exit 2 ;; esac
if [[ "$components" == full && "$f2c_mode" != required ]]; then
  echo 'full requires Fields2Cover; choose workbench or research for a deliberately reduced installation.' >&2
  exit 2
fi
simulation=0
research=0
[[ "$components" == workbench || "$components" == full ]] && simulation=1
[[ "$components" == research || "$components" == full ]] && research=1
wheel=src/lab/vendor/wheels/fields2cover-2.1.0-cp312-cp312-linux_x86_64.whl
install_f2c=0
if [[ "$f2c_mode" == required || ( "$f2c_mode" == auto && -f "$wheel" ) ]]; then install_f2c=1; fi
printf 'Selected components: %s; Fields2Cover: %s. This does not reproduce historical NAS environments.\n' "$components" "$f2c_mode"

wsl=0
if [[ -n "${WSL_DISTRO_NAME:-}" ]] || grep -qi microsoft /proc/version 2>/dev/null; then
  wsl=1
fi

if [[ ! -r /etc/os-release ]]; then
  echo '需要 Ubuntu 22.04。没有 /etc/os-release。' >&2
  exit 1
fi
# shellcheck disable=SC1091
source /etc/os-release
if [[ "${ID:-}" != "ubuntu" || "${VERSION_ID:-}" != "22.04" ]]; then
  echo "该安装器支持 Ubuntu 22.04。当前是 ${PRETTY_NAME:-unknown}。" >&2
  exit 1
fi

if [[ "$(uname -m)" != x86_64 || "$(dpkg --print-architecture)" != amd64 ]]; then
  echo 'Pinned Node and Fields2Cover artifacts support Ubuntu 22.04 amd64 only.' >&2
  exit 1
fi
if (( EUID == 0 )); then
  echo 'Run setup as the normal project user, not via sudo; package installation will request sudo itself.' >&2
  exit 1
fi

if ! command -v curl >/dev/null 2>&1 || ! command -v gpg >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y curl ca-certificates gnupg
fi

if (( simulation )); then
if [[ ! -f /usr/share/keyrings/ros-archive-keyring.gpg ]]; then
  sudo curl -fsSL https://raw.githubusercontent.com/ros/rosdistro/master/ros.key \
    -o /usr/share/keyrings/ros-archive-keyring.gpg
fi
if [[ ! -f /etc/apt/sources.list.d/ros2.list ]]; then
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/usr/share/keyrings/ros-archive-keyring.gpg] http://packages.ros.org/ros2/ubuntu jammy main" \
    | sudo tee /etc/apt/sources.list.d/ros2.list >/dev/null
fi

fi
sudo apt-get update
packages=(python3 curl ca-certificates gnupg git zstd)
add_packages() {
  local selected
  mapfile -t selected < <(grep -vE '^[[:space:]]*(#|$)' "$1")
  packages+=("${selected[@]}")
}
if (( simulation )); then add_packages "$root/deploy/ros-packages.txt"; fi
if (( research )); then add_packages "$root/deploy/research-packages.txt"; fi
if (( install_f2c )); then add_packages "$root/deploy/fields2cover-packages.txt"; fi
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y --no-install-recommends "${packages[@]}"
# Refresh JAVA_HOME after installing JDK 17; do not modify system alternatives.
source "$root/setup/ubuntu/env.sh"

if ! command -v uv >/dev/null 2>&1 || [[ "$(uv --version 2>/dev/null || true)" != *'0.12.23'* ]]; then
  curl -LsSf https://astral.sh/uv/0.12.23/install.sh | sh
  # shellcheck source=env.sh
  source "$root/setup/ubuntu/env.sh"
fi

node_prefix="${HOME}/.local/node-v24.22.0"
if ! command -v node >/dev/null 2>&1 || [[ "$(node --version)" != v24.22.0 ]]; then
  archive="$(mktemp)"
  curl -fsSL "https://nodejs.org/dist/v24.22.0/node-v24.22.0-linux-x64.tar.gz" -o "$archive"
  echo "28ea0a438de9a13bd7891fd758eee6c7819ee8af5442dfd8d45bd65e56d4228c  ${archive}" | sha256sum -c -
  mkdir -p "$node_prefix"
  tar -xzf "$archive" -C "$node_prefix" --strip-components=1
  rm -f "$archive"
  hash -r
fi

uv python install 3.12.13
uv sync --locked --extra solvers --python 3.12.13
(cd src/lab && uv sync --locked --inexact --python 3.12.13)
if (( install_f2c )); then
  if [[ "$f2c_mode" == required ]]; then
    python3 deploy/fetch-fields2cover.py
  else
    python3 deploy/fetch-fields2cover.py --check
  fi
  uv pip install --no-deps --python src/lab/.venv/bin/python "$wheel"
  src/lab/.venv/bin/python deploy/check-fields2cover.py
else
  echo 'Fields2Cover not installed by this selection; its coverage solvers are optional and unavailable if absent.'
fi
if (( research )); then
  bash deploy/build-cohort.sh
fi
(cd src/frontend && npm ci --engine-strict --no-audit --no-fund && npm run build)

if (( simulation )); then
  bash src/lab/ros/native/install-ros-extras.sh
  python3 deploy/record-sim-profiles.py
else
  echo 'ROS/Gazebo not selected. Numerical/planning workbench is available; physical simulation is not.'
fi
diagnostic_dir="$root/.local-state/deploy/runtime-$(date -u +%Y%m%dT%H%M%SZ)-$$"
python3 deploy/check-runtime.py --components "$components" --fields2cover "$f2c_mode" --output "$diagnostic_dir"
printf 'Selected setup stages completed: %s. Fuel assets and real Gazebo acceptance remain separate.\n' "$components"

if [[ "$wsl" -eq 1 ]]; then
  cat <<'EOF'
这是 WSL2。Windows 浏览器通过 localhost 转发访问 http://localhost:4100/ 。
Gazebo 默认软件渲染。若 `nvidia-smi` 在 WSL 里可用，启动前可以 export LIBGL_ALWAYS_SOFTWARE=0。
内存限制见 setup/ubuntu/wslconfig.example，改完后在 PowerShell 执行 wsl --shutdown。
启动：bash setup/ubuntu/run-lab.sh
EOF
else
  cat <<'EOF'
这是原生 Ubuntu 22.04。网关只监听 127.0.0.1。有 NVIDIA 驱动时直接用 GPU，不强制软件渲染。
没有可用 GPU 时启动前 export LIBGL_ALWAYS_SOFTWARE=1。
启动：bash setup/ubuntu/run-lab.sh
EOF
fi

