#!/usr/bin/env bash
# Browser check of the research agent panel (CONTRACT §9) without an external model API or ROS.
# Starts stub_llm.py on 4291, a gateway with the stub as its model on 4199 and one without a model on 4198,
# each with its own runtime under OUT, then runs browser_check.cjs with Playwright's Chromium.
#
#   npm run build --prefix src/frontend
#   bash setup/2026-10-29/agent/browser_check.sh /新的绝对输出目录
#
# node must resolve `playwright` (cloud sessions: NODE_PATH=/opt/node22/lib/node_modules).
set -euo pipefail
out="${1:?用法：browser_check.sh /新的绝对输出目录}"
case "$out" in /*) ;; *) echo "输出目录须为绝对路径：$out" >&2; exit 2 ;; esac
[ ! -e "$out" ] || { echo "输出目录已存在，请换一个新目录：$out" >&2; exit 2; }
here="$(cd "$(dirname "$0")" && pwd)"
repo="$(cd "$here/../../.." && pwd)"
py="$repo/src/lab/.venv/bin/python"
[ -x "$py" ] || { echo "缺少 src/lab/.venv（在 src/lab 运行 uv sync --locked --inexact）" >&2; exit 2; }
[ -f "$repo/src/frontend/dist/index.html" ] || { echo "缺少前端构建（npm run build --prefix src/frontend）" >&2; exit 2; }
for port in 4291 4199 4198; do
  if (exec 3<>"/dev/tcp/127.0.0.1/$port") 2>/dev/null; then echo "端口 $port 已被占用" >&2; exit 2; fi
done
mkdir -p "$out"
key=stub-not-a-secret          # a stand-in value: the check proves it is never recorded

pids=()
cleanup() {
  for pid in "${pids[@]}"; do kill -TERM "$pid" 2>/dev/null || true; done
  for pid in "${pids[@]}"; do wait "$pid" 2>/dev/null || true; done
}
trap cleanup EXIT

"$py" "$here/stub_llm.py" 4291 > "$out/stub.log" 2>&1 &
pids+=($!)
(cd "$repo/src/lab" && URP_LAB_RUNTIME="$out/runtime" URP_LLM_BASE_URL=http://127.0.0.1:4291/v1 \
  URP_LLM_API_KEY="$key" URP_LLM_MODEL=stub-script exec "$py" -m urp_lab serve --port 4199 --no-archive) \
  > "$out/gateway.log" 2>&1 &
pids+=($!)
(cd "$repo/src/lab" && exec env -u URP_LLM_API_KEY -u ZHIPU_API_KEY -u URP_LLM_BASE_URL -u URP_LLM_MODEL \
  URP_LAB_RUNTIME="$out/runtime-unconfigured" "$py" -m urp_lab serve --port 4198 --no-archive) \
  > "$out/gateway-unconfigured.log" 2>&1 &
pids+=($!)
for port in 4199 4198; do
  for _ in $(seq 1 120); do
    "$py" -c "import urllib.request,sys; urllib.request.urlopen('http://127.0.0.1:$port/lab/api/health', timeout=1)" 2>/dev/null && break
    sleep 0.5
  done
done

git -C "$repo" rev-parse HEAD > "$out/commit.txt"
git -C "$repo" status --porcelain --untracked-files=no >> "$out/commit.txt"
status=0
AGENT_CHECK_KEY="$key" node "$here/browser_check.cjs" "$out" || status=$?
exit "$status"
