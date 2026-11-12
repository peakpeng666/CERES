#!/usr/bin/env bash
# Pack redistributable files that are not in git. Fuel models are never packed:
# Warehouse and shelf_big are CC BY-NC-ND, and MovAi shelf is CC BY-NC.
# See deploy/THIRD_PARTY.md.
#
#   bash deploy/pack-assets.sh
# The tarball is for the owner's own restore of laptop-only binaries plus an
# optional Fields2Cover wheel. Do not attach it to a public Release if it
# contains those laptop binaries. Public upload is deploy/upload-assets.sh.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
out="${1:-$root/deploy/ceres-sim-assets.tar.gz}"
cd "$root"

if [[ -e setup/2026-10-15/cache/ignition-fuel || -e setup/2026-10-15/cache/fuel-cache.tar.zst ]]; then
  printf '%s\n' "Fuel 缓存还在本机，但不会写入这个包。Warehouse 与 shelf_big 是 CC BY-NC-ND，MovAi shelf 是 CC BY-NC，不能放进公开镜像或公开 Release。搭档用 ceres fuel 自己向 fuel.gazebosim.org 下载。"
fi

optional=(
  src/lab/vendor/wheels/fields2cover-2.1.0-cp312-cp312-linux_x86_64.whl
)
laptop_only=(
  setup/2026-10-15/deployment/runtime/ros-extras
  setup/2026-10-15/deployment/runtime/tf-repro/liburp_tf_buffer.so
  setup/2026-10-15/deployment/runtime/liburp_gui_lifetime.so
  src/lab/runtime/native/bridge/parameter_bridge
)

present=()
notes=()
consider() {
  local kind="$1" path="$2" bytes="0"
  if [[ -e "$path" ]]; then
    present+=("$path")
    bytes="$(du -sb "$path" | awk '{print $1}')"
    notes+=("$kind"$'\t'"$bytes"$'\t'"$path")
  else
    notes+=("absent"$'\t'"0"$'\t'"$path")
  fi
}
for path in "${optional[@]}"; do consider optional "$path"; done
for path in "${laptop_only[@]}"; do consider laptop-only "$path"; done

if ((${#present[@]} == 0)); then
  printf '%s\n' "没有可打包的本机文件。Fuel 缓存按许可排除。Fields2Cover 轮子不在这台检出里。"
  exit 0
fi

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/ceres-sim-assets"
cp deploy/THIRD_PARTY.md "$stage/ceres-sim-assets/THIRD_PARTY.md"
{
  echo "repo=$root"
  echo "created_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "fuel=excluded"
  printf 'kind\tbytes\tpath\n'
  printf '%s\n' "${notes[@]}"
} > "$stage/ceres-sim-assets/MANIFEST.tsv"
tar -C "$root" -cf - "${present[@]}" | tar -C "$stage/ceres-sim-assets" -xf -
mkdir -p "$(dirname "$out")"
tar -C "$stage" -czf "$out" ceres-sim-assets
echo "已写出 $out"
echo "这个包不是公开的 Fuel 分发。公开附件只用 bash deploy/upload-assets.sh，并且只在 Fields2Cover 轮子和 BSD 声明都在的时候上传。"
echo "laptop-only 二进制只在主人那套系统库上通过验收，不要放进公开 Release。"
