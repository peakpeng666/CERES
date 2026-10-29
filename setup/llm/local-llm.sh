#!/usr/bin/env bash
# 本地开源模型服务（Ollama，用户目录安装、仅监听回环地址、无需 sudo）。
# 用法：bash setup/llm/local-llm.sh start|stop|status|pull [模型]
set -euo pipefail

version="${URP_OLLAMA_VERSION:-v0.40.13}"
root="${URP_OLLAMA_ROOT:-$HOME/.local/opt/ollama/$version}"
state="${XDG_STATE_HOME:-$HOME/.local/state}/urp-llm"
model="${2:-${URP_LOCAL_MODEL:-qwen3.8:27b}}"
export OLLAMA_HOST="${OLLAMA_HOST:-127.0.0.1:11434}"
export OLLAMA_MODELS="${OLLAMA_MODELS:-$HOME/.ollama/models}"
export OLLAMA_KEEP_ALIVE="${OLLAMA_KEEP_ALIVE:-10m}"
export OLLAMA_CONTEXT_LENGTH="${OLLAMA_CONTEXT_LENGTH:-16384}"
bin="$root/bin/ollama"
mkdir -p "$state"

running() { [[ -f "$state/ollama.pid" ]] && kill -0 "$(cat "$state/ollama.pid")" 2>/dev/null; }

case "${1:-status}" in
  start)
    [[ -x "$bin" ]] || { printf '未找到 %s；请按 setup/README.md「本地模型」一节安装。\n' "$bin" >&2; exit 1; }
    if running; then printf '已在运行（PID %s，%s）\n' "$(cat "$state/ollama.pid")" "$OLLAMA_HOST"; exit 0; fi
    setsid nohup "$bin" serve >>"$state/ollama.log" 2>&1 < /dev/null &
    echo $! > "$state/ollama.pid"
    for _ in $(seq 1 50); do
      if curl -fsS -m 1 "http://$OLLAMA_HOST/api/version" >/dev/null 2>&1; then printf '本地模型服务已启动：http://%s（日志 %s）\n' "$OLLAMA_HOST" "$state/ollama.log"; exit 0; fi
      sleep 0.2
    done
    printf '服务未在 10 s 内响应，见 %s\n' "$state/ollama.log" >&2; exit 1 ;;
  stop)
    if running; then kill "$(cat "$state/ollama.pid")"; rm -f "$state/ollama.pid"; printf '已停止\n'; else printf '未运行\n'; fi ;;
  status)
    if running; then printf '运行中（PID %s）\n' "$(cat "$state/ollama.pid")"; curl -fsS -m 2 "http://$OLLAMA_HOST/api/tags" | python3 -c 'import json,sys; [print(" ", m["name"], round(m["size"]/1e9,1), "GB") for m in json.load(sys.stdin).get("models",[])]'; else printf '未运行\n'; fi ;;
  pull)
    running || "$0" start
    "$bin" pull "$model" ;;
  *) printf '用法：%s start|stop|status|pull [模型]\n' "$0" >&2; exit 2 ;;
esac
