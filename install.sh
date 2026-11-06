#!/usr/bin/env bash
# Public one-command install for the URP workbench and Gazebo stack.
#   curl -fsSL https://raw.githubusercontent.com/peakpeng666/URP/main/install.sh | bash
# No GitHub login and no docker login. Docker pulls ghcr.io/peakpeng666/urp-sim
# and does not clone the repository. Ubuntu 22.04 without Docker uses a shallow
# sparse clone. See DEPLOY.md.
# Select a release/commit: URP_REF=v20261019 bash install.sh
# Or: bash install.sh --ref main up (native); Docker requires a published tag.
set -euo pipefail

REPO=https://github.com/peakpeng666/URP.git
RAW_ROOT=https://raw.githubusercontent.com/peakpeng666/URP
export GIT_TERMINAL_PROMPT=0
export DOCKER_CLI_HINTS=false

say() { printf '%s\n' "$*"; }
die() { printf '%s\n' "$*" >&2; exit 1; }

configure() {
  STATE="${URP_HOME:-${HOME}/.local/share/urp}"
  BIN="${HOME}/.local/bin/urp"
  local saved_ref= saved_raw= saved_tag= saved_components= saved_port= explicit_ref="${URP_REF:-}"
  if [[ -f "$STATE/install.selection" ]]; then
    { IFS= read -r saved_ref || true; IFS= read -r saved_raw || true
      IFS= read -r saved_tag || true; IFS= read -r saved_components || true
      IFS= read -r saved_port || true
    } < "$STATE/install.selection"
  fi
  cmd=up
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --ref) [[ $# -ge 2 ]] || die '--ref 需要版本、分支或完整提交号'; explicit_ref="$2"; shift 2 ;;
      up|down|update|status|fuel) cmd="$1"; shift ;;
      *) die '用法：urp [--ref main|v版本|提交号|分支] up|down|update|status|fuel' ;;
    esac
  done
  REF="${explicit_ref:-${saved_ref:-main}}"
  # A raw override on the official host also identifies the source ref.
  # A script piped from a URL cannot discover its download URL: select --ref
  # (or URP_REF) when using a non-default installer, including a release tag.
  if [[ -n "${URP_RAW:-}" && "$URP_RAW" == "$RAW_ROOT/"* ]]; then
    local raw_ref="${URP_RAW#"$RAW_ROOT/"}"
    raw_ref="${raw_ref%/}"
    [[ -z "$explicit_ref" || "$explicit_ref" == "$raw_ref" ]] || die 'URP_RAW 与 URP_REF/--ref 不一致。'
    REF="$raw_ref"
  elif [[ -n "${URP_RAW:-}" && -z "$explicit_ref" ]]; then
    die '自定义 URP_RAW 必须同时明确设置 URP_REF。'
  fi
  [[ "$REF" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ && "$REF" != *..* && "$REF" != *//* && "$REF" != */ && "$REF" != *. ]] || die "无效的 ref：$REF"
  RAW="${URP_RAW:-$RAW_ROOT/$REF}"
  if [[ -z "${URP_RAW:-}" && "$REF" == "$saved_ref" && -n "$saved_raw" ]]; then RAW="$saved_raw"; fi
  [[ "$RAW" == https://* && "$RAW" != *$'\n'* && "$RAW" != *$'\r'* ]] || die 'URP_RAW 必须为单行 HTTPS URL。'
  RAW="${RAW%/}"
  ASSET_RAW="$RAW"
  TAG="${URP_IMAGE_TAG:-}"
  if [[ -z "$TAG" && "$REF" == "$saved_ref" ]]; then TAG="$saved_tag"; fi
  if [[ -z "$TAG" && ( "$REF" == main || "$REF" == v* || "$REF" =~ ^[0-9a-f]{40}$ ) && "$REF" != */* ]]; then TAG="$REF"; fi
  [[ -z "$TAG" || "$TAG" =~ ^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$ ]] || die "无效的镜像标签：$TAG"
  COMPONENTS="${URP_COMPONENTS:-${saved_components:-full}}"
  case "$COMPONENTS" in workbench|research|full) ;; *) die 'URP_COMPONENTS 只能是 workbench、research 或 full。' ;; esac
  PORT="${URP_PORT:-${saved_port:-4100}}"
  [[ "$PORT" =~ ^[0-9]{1,5}$ ]] && (( 10#$PORT >= 1 && 10#$PORT <= 65535 )) || die 'URP_PORT 必须为 1–65535。'
  HEALTH="http://127.0.0.1:${PORT}/lab/api/health"
  PAGE="http://localhost:${PORT}/"
  SIM_IMAGE="ghcr.io/peakpeng666/urp-sim:${TAG}"
  export URP_REF="$REF" URP_IMAGE_TAG="$TAG" URP_COMPONENTS="$COMPONENTS" URP_PORT="$PORT"
}

save_selection() {
  local pending
  mkdir -p "$STATE"
  pending="$(mktemp "$STATE/install.selection.XXXXXX")"
  printf '%s\n' "$REF" "$RAW" "$TAG" "$COMPONENTS" "$PORT" > "$pending"
  mv "$pending" "$STATE/install.selection"
}

require_curl() {
  command -v curl >/dev/null 2>&1 || die '缺少 curl。Ubuntu/WSL 请先执行 sudo apt-get update && sudo apt-get install -y curl ca-certificates git，再重试。'
}

bootstrap_native_tools() {
  if command -v curl >/dev/null 2>&1 && command -v git >/dev/null 2>&1; then return; fi
  say '需要安装基础包 curl、ca-certificates 和 git。'
  if [[ "$EUID" == 0 ]]; then
    apt-get update
    apt-get install -y curl ca-certificates git
  elif [[ -t 0 || -t 2 ]] && command -v sudo >/dev/null 2>&1; then
    sudo apt-get update
    sudo apt-get install -y curl ca-certificates git
  else
    die '请先在 Ubuntu 终端执行 sudo apt-get update && sudo apt-get install -y curl ca-certificates git，然后重试。'
  fi
}

require_native_platform() {
  ubuntu_2204 || die '原生安装需要 Ubuntu 22.04。'
  [[ "$(uname -m)" == x86_64 ]] || die '原生安装目前只支持 x86_64，不支持 ARM。'
  local kernel
  kernel="$(uname -r)"
  if [[ -n "${WSL_DISTRO_NAME:-}" || "${kernel,,}" == *microsoft* ]]; then
    [[ "${kernel,,}" == *microsoft-standard* || "${kernel,,}" == *wsl2* ]] || die '需要 WSL2；请在 Windows 用 wsl --list --verbose 核对，并先备份再转换 WSL1。'
  fi
}

require_image_tag() {
  [[ -n "$TAG" ]] || die "ref=$REF 没有默认发布镜像。请将 URP_REF 设为已发布的 v* 版本或完整提交号，或明确设置与该源码匹配的 URP_IMAGE_TAG；不会回落到其他通道。"
}

visibility_help() {
  cat >&2 <<'EOF'
匿名拉取失败，请检查网络、镜像标签及包的公开状态。
镜像发布者可检查：
  https://github.com/users/peakpeng666/packages/container/urp-sim/settings
  https://github.com/users/peakpeng666/packages/container/urp-bench/settings
EOF
}

have_docker() {
  command -v docker >/dev/null 2>&1 || return 1
  docker info >/dev/null 2>&1
}

ubuntu_2204() {
  [[ -r /etc/os-release ]] || return 1
  # shellcheck disable=SC1091
  source /etc/os-release
  [[ "${ID:-}" == "ubuntu" && "${VERSION_ID:-}" == "22.04" ]]
}

install_cli() {
  local pending
  mkdir -p "$(dirname "$BIN")" "$STATE/deploy"
  pending="$(mktemp "${BIN}.XXXXXX")"
  if ! curl -fsSL "${ASSET_RAW}/install.sh" -o "$pending"; then
    rm -f "$pending"
    die '下载所选版本的命令入口失败；保留原入口。'
  fi
  chmod 755 "$pending"
  mv "$pending" "$BIN"
}

fetch_compose() {
  local stage name
  local -a names=(docker-compose.sim.yml docker-compose.gpu.yml docker-compose.display.yml docker-compose.fuel.yml fetch-fuel.sh)
  mkdir -p "$STATE/deploy"
  stage="$(mktemp -d "$STATE/deploy/.download.XXXXXX")"
  for name in "${names[@]}"; do
    if ! curl -fsSL "${ASSET_RAW}/deploy/$name" -o "$stage/$name"; then
      rm -rf -- "$stage"
      die "部署文件下载失败：$name；原文件保留。"
    fi
  done
  chmod 755 "$stage/fetch-fuel.sh"
  # Publish only after every download succeeds. A network error cannot truncate
  # the compose files needed by an existing installation's down/status command.
  for name in "${names[@]}"; do mv "$stage/$name" "$STATE/deploy/$name"; done
  rmdir "$stage"
}

compose() {
  docker compose -f "$STATE/deploy/docker-compose.sim.yml" "$@"
}

compose_files() {
  local -a files=(-f "$STATE/deploy/docker-compose.sim.yml")
  if [[ "${URP_GPU:-0}" == "1" && -f "$STATE/deploy/docker-compose.gpu.yml" ]]; then
    files+=(-f "$STATE/deploy/docker-compose.gpu.yml")
  fi
  if [[ "${URP_DISPLAY:-0}" == "1" && -f "$STATE/deploy/docker-compose.display.yml" ]]; then
    files+=(-f "$STATE/deploy/docker-compose.display.yml")
    export DISPLAY="${DISPLAY:-:0}"
  fi
  if [[ -d "${URP_FUEL_DIR:-$STATE/fuel}/fuel.gazebosim.org" ]]; then
    files+=(-f "$STATE/deploy/docker-compose.fuel.yml")
    export URP_FUEL_DIR="${URP_FUEL_DIR:-$STATE/fuel}"
  fi
  docker compose "${files[@]}" "$@"
}

pull_sim() {
  if docker pull "$SIM_IMAGE" </dev/null; then
    return 0
  fi
  if docker image inspect "$SIM_IMAGE" >/dev/null 2>&1; then
    say "注册表暂时拉不到，使用本机已有的 ${SIM_IMAGE}。"
    return 0
  fi
  visibility_help
  return 1
}

wait_health() {
  local i
  for i in $(seq 1 90); do
    if curl -fsS "$HEALTH" 2>/dev/null | grep -q '"status":"ok"'; then
      return 0
    fi
    sleep 2
  done
  say "健康检查还没有返回 ${HEALTH}" >&2
  compose logs --tail 60 >&2 || true
  return 1
}

open_page() {
  say "$PAGE"
  if [[ -n "${WSL_DISTRO_NAME:-}" ]] && command -v wslview >/dev/null 2>&1; then
    wslview "$PAGE" >/dev/null 2>&1 || true
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$PAGE" >/dev/null 2>&1 || true
  fi
}

resolve_image_source() {
  local revision
  revision="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$SIM_IMAGE")"
  [[ "$revision" =~ ^[0-9a-f]{40}$ ]] || die '镜像缺少有效源码 revision 标签；拒绝混用未知版本文件。'
  if [[ "$REF" =~ ^[0-9a-f]{40}$ && "$revision" != "$REF" ]]; then
    die "镜像 revision=$revision 与请求的 ref=$REF 不一致。"
  fi
  if [[ "$TAG" =~ ^[0-9a-f]{40}$ && "$revision" != "$TAG" ]]; then
    die "镜像 revision=$revision 与请求的镜像提交标签=$TAG 不一致。"
  fi
  if [[ "$RAW" == "$RAW_ROOT/$REF" ]]; then ASSET_RAW="$RAW_ROOT/$revision"; fi
  say "镜像源码：$revision；部署文件：$ASSET_RAW"
}

require_docker_platform() {
  local platform
  platform="$(docker info --format '{{.OSType}}/{{.Architecture}}')"
  [[ "$platform" == linux/x86_64 || "$platform" == linux/amd64 ]] || die "需要 Linux x86_64 Docker Engine，当前：$platform"
}

docker_up() {
  require_image_tag
  require_curl
  require_docker_platform
  say "安装来源：$REF；镜像：$SIM_IMAGE"
  pull_sim
  resolve_image_source
  fetch_compose
  compose_files up -d --no-build --pull never --remove-orphans
  wait_health
  install_cli
  save_selection
  open_page
  say "已在后台运行。停止：urp down    状态：urp status    更新：urp update"
  say "若 shell 找不到 urp，把 ~/.local/bin 加入 PATH。"
}

clone_src() {
  local src="$STATE/src" existing_ref=
  command -v git >/dev/null 2>&1 || die '缺少 git。请先执行 sudo apt-get update && sudo apt-get install -y git ca-certificates curl，再重试。'
  if [[ ! -d "$src/.git" ]]; then
    [[ ! -e "$src" || -z "$(ls -A "$src")" ]] || die "$src 已存在且不是安装器的 Git 检出；不会覆盖。"
    mkdir -p "$src"
    git -C "$src" init --quiet >&2 || return $?
    git -C "$src" remote add origin "$REPO" || return $?
    git -C "$src" sparse-checkout init --no-cone >&2 || return $?
    git -C "$src" sparse-checkout set --no-cone \
      '/*' \
      '!/src/repos/' \
      '!/data/bpp/' \
      '!/data/cifar-training/' \
      '!/experiments/' \
      '!/papers/' \
      '!/literature/' \
      '!/materials/' >&2 || return $?
  fi
  [[ "$(git -C "$src" remote get-url origin)" == "$REPO" ]] || die '现有检出的 origin 与 URP 仓库不一致；不会改写。'
  [[ ! -f "$STATE/source.ref" ]] || IFS= read -r existing_ref < "$STATE/source.ref"
  if [[ "$existing_ref" != "$REF" || "${URP_UPDATE:-0}" == 1 ]] || ! git -C "$src" rev-parse --verify HEAD >/dev/null 2>&1; then
    update_src "$src" || return $?
  fi
  printf '%s\n' "$src"
}

update_src() {
  local src="$1"
  [[ -z "$(git -C "$src" status --porcelain)" ]] || die '检出中存在本地修改或未跟踪文件；请先保存，安装器不会覆盖。'
  git -C "$src" fetch --depth 1 --filter=blob:none origin "$REF" >&2 || return $?
  # FETCH_HEAD is the exact result of this fetch, including tags and commits;
  # A remote-tracking ref may still point at an older commit after an explicit fetch.
  git -C "$src" checkout --detach --quiet FETCH_HEAD >&2 || return $?
  printf '%s\n' "$REF" > "$STATE/source.ref"
}

native_up() {
  [[ "$EUID" != 0 ]] || die '请使用普通 Ubuntu 用户运行安装器；系统依赖安装时会提示输入 sudo 密码。'
  require_native_platform
  bootstrap_native_tools
  require_curl
  local src revision ready=
  src="$(clone_src)"
  revision="$(git -C "$src" rev-parse HEAD)"
  if [[ "$RAW" == "$RAW_ROOT/$REF" ]]; then ASSET_RAW="$RAW_ROOT/$revision"; fi
  [[ ! -f "$STATE/source.setup" ]] || IFS= read -r ready < "$STATE/source.setup"
  say "安装来源：$REF ($revision)；组件：$COMPONENTS"
  if [[ "$ready" != "$revision $COMPONENTS" || "${URP_UPDATE:-0}" == 1 ]]; then
    bash "$src/setup/ubuntu/setup.sh" --components "$COMPONENTS"
    printf '%s\n' "$revision $COMPONENTS" > "$STATE/source.setup"
  fi
  install_cli
  save_selection
  say "$PAGE"
  if curl -fsS "$HEALTH" >/dev/null 2>&1; then
    bash "$src/setup/run.sh" lab-stop || true
  fi
  # Foreground so this terminal owns the process. The URL is printed above.
  exec bash "$src/setup/ubuntu/run-lab.sh"
}

route() {
  # Keep down/status usable for existing installations. Never silently switch
  # a saved source channel, or try to fetch a retired branch for new work.
  if [[ ( "$REF" == deploy || "$REF" == redo ) && ( "$cmd" == up || "$cmd" == update || "$cmd" == fuel ) ]]; then
    die '旧分支安装选择需要迁移：请重新取得 main/install.sh，再运行 --ref main update，或选择已验证发布的完整提交号；原数据与选择保留。'
  fi
  case "$cmd" in
    up|update)
      [[ "$cmd" == update ]] && export URP_UPDATE=1
      if have_docker; then
        docker_up
      elif ubuntu_2204; then
        native_up
      else
        die "需要 Docker，或 Ubuntu 22.04（含 WSL2 里的 Ubuntu 22.04）。当前系统没有这两条路。"
      fi
      ;;
    down)
      if have_docker && [[ -f "$STATE/deploy/docker-compose.sim.yml" ]]; then
        compose stop
        say "容器已停止。数据卷还在。删除卷才用：docker compose -f $STATE/deploy/docker-compose.sim.yml down -v"
      elif ubuntu_2204 && [[ -d "$STATE/src" ]]; then
        bash "$STATE/src/setup/run.sh" lab-stop
      else
        die "没有找到这一台机器上由 urp 启动的工作台。"
      fi
      ;;
    status)
      require_curl
      if have_docker && [[ -f "$STATE/deploy/docker-compose.sim.yml" ]]; then
        compose ps || true
      fi
      curl -fsS "$HEALTH" || die "健康检查失败：${HEALTH}"
      say
      ;;
    fuel)
      require_curl
      local dest
      if have_docker; then
        require_image_tag
        require_docker_platform
        pull_sim
        resolve_image_source
        install_cli
        fetch_compose
        dest="${URP_FUEL_DIR:-$STATE/fuel}"
        bash "$STATE/deploy/fetch-fuel.sh" "$dest"
        export URP_FUEL_DIR="$dest"
        if docker image inspect "$SIM_IMAGE" >/dev/null 2>&1 || pull_sim; then
          compose_files up -d --no-build --pull never --remove-orphans
          save_selection
          say "Fuel 已挂进容器。maze 本来就不需要这些模型。warehouse 与 depot 重新开仿真会话后才会读到。"
        fi
      elif ubuntu_2204; then
        require_native_platform
        bootstrap_native_tools
        local src
        src="$(clone_src)"
        if [[ "$RAW" == "$RAW_ROOT/$REF" ]]; then ASSET_RAW="$RAW_ROOT/$(git -C "$src" rev-parse HEAD)"; fi
        install_cli
        dest="$src/setup/2026-10-15/cache/ignition-fuel"
        curl -fsSL "${ASSET_RAW}/deploy/fetch-fuel.sh" -o "$STATE/deploy/fetch-fuel.sh"
        chmod 755 "$STATE/deploy/fetch-fuel.sh"
        bash "$STATE/deploy/fetch-fuel.sh" "$dest"
        save_selection
        say "模型在仓库缓存里。重新启动 warehouse 或 depot 会话后生效。maze 不需要 Fuel。"
      else
        die "下载 Fuel 需要 Docker 或 Ubuntu 22.04。"
      fi
      ;;
    *)
      die "用法：urp up | down | update | status | fuel"
      ;;
  esac
}

main() { configure "$@"; route; }
if [[ "${BASH_SOURCE[0]:-}" == "$0" || -z "${BASH_SOURCE[0]:-}" ]]; then main "$@"; fi

