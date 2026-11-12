#!/usr/bin/env bash
# Download Gazebo Fuel models from fuel.gazebosim.org into a local cache.
# This is the user's own copy. It does not upload anything and it is not run
# while building images or Release assets.
#
# Warehouse (OpenRobotics, CC BY-NC-ND 4.0), shelf_big (MovAi, CC BY-NC-ND 4.0)
# and shelf (MovAi, CC BY-NC 4.0) may be downloaded here for local simulation.
# They must not be copied into a public image or a public GitHub Release.
# maze does not reference Fuel and does not need this script.
#
#   bash deploy/fetch-fuel.sh [cache-directory]
#   CERES_FUEL_ONLY=Table bash deploy/fetch-fuel.sh /tmp/fuel
set -euo pipefail

dest="${1:-${CERES_FUEL_DIR:-$PWD/setup/2026-10-15/cache/ignition-fuel}}"
mkdir -p "$dest"

if ! command -v python3 >/dev/null 2>&1 || ! command -v curl >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y python3 ca-certificates curl
fi

# owner, name, version, license recorded on 2026-10-19 from the Fuel API.
models=(
  "OpenRobotics|Warehouse|5|CC BY-NC-ND 4.0|no"
  "MovAi|shelf_big|2|CC BY-NC-ND 4.0|no"
  "MovAi|shelf|1|CC BY-NC 4.0|no"
  "OpenRobotics|Depot|6|CC BY 4.0|yes"
  "OpenRobotics|Chair|3|CC BY 4.0|yes"
  "OpenRobotics|foldable_chair|3|CC BY 4.0|yes"
  "OpenRobotics|MaleVisitorOnPhone|2|CC BY 4.0|yes"
  "OpenRobotics|Table|4|CC0 1.0|yes"
  "OpenRobotics|Jersey Barrier|6|CC0 1.0|yes"
  "plateau|Casual female|1|CC0 1.0|yes"
)

only="${CERES_FUEL_ONLY:-}"
printf '%s\n' "缓存目录：$dest"
printf '%s\n' "这些文件只留在本机。带 no 的模型不允许放进公开镜像或公开 Release。"

for row in "${models[@]}"; do
  IFS='|' read -r owner name version license redistribute <<<"$row"
  if [[ -n "$only" && "$only" != "$name" && "$only" != "$owner/$name" ]]; then
    continue
  fi
  python3 - "$dest" "$owner" "$name" "$version" "$license" "$redistribute" <<'PY'
import json, pathlib, sys, urllib.parse, urllib.request, zipfile
dest, owner, name, version, license, redistribute = sys.argv[1:]
owner_lc = owner.lower()
name_lc = name.lower()
root = pathlib.Path(dest) / "fuel.gazebosim.org" / owner_lc / "models" / name_lc / version
marker = root / "model.sdf"
if marker.is_file():
    print(f"已有 {owner}/{name}@{version}")
    sys.exit(0)
enc = urllib.parse.quote(name)
meta_url = f"https://fuel.gazebosim.org/1.0/{urllib.parse.quote(owner)}/models/{enc}"
zip_url = f"https://fuel.gazebosim.org/1.0/{urllib.parse.quote(owner)}/models/{enc}/{version}/{enc}.zip"
print(f"下载 {owner}/{name} 版本 {version}（登记许可 {license}，公开再分发={redistribute}）")
print(f"  {zip_url}")
with urllib.request.urlopen(meta_url, timeout=60) as response:
    meta = json.load(response)
live = meta.get("license_name") or meta.get("license_url") or "unknown"
if str(meta.get("version")) not in (str(version),):
    print(f"  Fuel 当前 tip 版本是 {meta.get('version')}，仍下载钉住的版本 {version}。")
print(f"  Fuel 返回的许可：{live}")
root.mkdir(parents=True, exist_ok=True)
archive = root / "_model.zip"
urllib.request.urlretrieve(zip_url, archive)
with zipfile.ZipFile(archive) as bundle:
    bundle.extractall(root)
archive.unlink()
if not marker.is_file():
    sys.exit(f"解压后没有 model.sdf：{root}")
(root / "LICENSE.fuel.txt").write_text(
    f"{owner}/{name} version {version}\nrecorded={license}\nlive={live}\n"
    f"redistribute_in_public_image_or_release={redistribute}\n"
    "Downloaded by the end user from fuel.gazebosim.org. Do not upload this directory.\n",
    encoding="utf-8",
)
print(f"  已写入 {root}")
PY
done

printf '%s\n' "完成。请不要把这个目录打进镜像、pack-assets 或 GitHub Release。"
