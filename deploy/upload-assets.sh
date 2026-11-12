#!/usr/bin/env bash
# Owner script. Uploads a public Release asset only when the file may be
# redistributed. Fuel caches and NC/ND models are refused.
#
#   bash deploy/upload-assets.sh vYYYYMMDD
# Requires the owner's own gh auth. Collaborators never run this.
set -euo pipefail
root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)"
cd "$root"
tag="${1:-}"
wheel="src/lab/vendor/wheels/fields2cover-2.1.0-cp312-cp312-linux_x86_64.whl"
expected="0fe53e459024b282be455b2a4bdbca3d93efa029539ff87c88c1105361344c19"

refuse() {
  printf '%s\n' "$*" >&2
  exit 2
}

for path in "$@"; do
  case "$path" in
    *ignition-fuel*|*fuel-cache*|*Warehouse*|*shelf*)
      refuse "拒绝上传 $path。Fuel 模型不能进公开 Release。见 deploy/THIRD_PARTY.md。"
      ;;
  esac
done

if [[ -e setup/2026-10-15/cache/ignition-fuel ]]; then
  printf '%s\n' "本机有 Fuel 缓存。脚本不会读取或上传它。"
fi

if [[ ! -f "$wheel" ]]; then
  printf '%s\n' "没有 Fields2Cover 轮子，因此没有可公开再分发的附件。Foxglove 与 rosx 随镜像里的 apt 包分发，不单独上传。Fuel 不上传。"
  exit 0
fi

actual="$(sha256sum "$wheel" | awk '{print $1}')"
if [[ "$actual" != "$expected" ]]; then
  refuse "轮子 SHA256 是 $actual，与清单里的 $expected 不一致。不上传。"
fi
if ! grep -q 'BSD-3-Clause' deploy/THIRD_PARTY.md; then
  refuse "deploy/THIRD_PARTY.md 里没有 BSD-3-Clause 声明。不上传轮子。"
fi

stage="$(mktemp -d)"
trap 'rm -rf "$stage"' EXIT
mkdir -p "$stage/ceres-fields2cover"
cp "$wheel" deploy/THIRD_PARTY.md "$stage/ceres-fields2cover/"
(
  cd "$stage/ceres-fields2cover"
  sha256sum fields2cover-2.1.0-cp312-cp312-linux_x86_64.whl THIRD_PARTY.md > SHA256SUMS
)
archive="$root/deploy/release-assets/ceres-fields2cover.tar.gz"
mkdir -p "$(dirname "$archive")"
tar -C "$stage" -czf "$archive" ceres-fields2cover
sha256sum "$archive" | tee "$archive.sha256"
printf '%s\n' "已写出 $archive。许可是 BSD-3-Clause，声明在包内的 THIRD_PARTY.md。"

if [[ -z "$tag" ]]; then
  printf '%s\n' "未给出 Release 标签，所以没有上传。用法：bash deploy/upload-assets.sh vYYYYMMDD"
  exit 0
fi
command -v gh >/dev/null 2>&1 || refuse "没有 gh，包留在本地。"
gh release upload "$tag" "$archive" "$archive.sha256" --clobber
printf '%s\n' "已上传到 $tag。确认这个 Release 是公开的。"
