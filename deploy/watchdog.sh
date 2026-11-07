#!/usr/bin/env bash
# 看门狗：只兜 serve（面板 API）。由 cron 每 2 分钟调用。
#   2026-11-07 起改为 GitHub Actions 主跑数据，本机的 watch/guard 不再常驻，
#   所以这里绝不能把 watch/guard 拉起来，否则会和 CI 抢数据、重复推送。
#   判断：pid 存活 + /api/health 可响应
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
LOG="$ROOT/data/log/watchdog.log"
mkdir -p "$ROOT/data/log"

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$1" >> "$LOG"; }

rotate() {
  local f="$1" limit="$2"
  [[ -f "$f" ]] || return 0
  [[ $(stat -c%s "$f") -gt "$limit" ]] || return 0
  mv "$f" "$f.old"
  log "rotated $(basename "$f")"
}

# 常年跑别把磁盘写满
rotate "$ROOT/data/log/serve.log" 10485760
rotate "$ROOT/data/watch.log" 10485760
rotate "$ROOT/data/log/watch.log.boot" 1048576

PORT=$(grep -E '^NBAQ_PORT=' "$ROOT/.env" 2>/dev/null | cut -d= -f2 | tr -d ' ')
PORT=${PORT:-8000}
if curl -fsS --max-time 3 "http://127.0.0.1:${PORT}/api/health" >/dev/null 2>&1; then
  exit 0
fi

log "serve unhealthy, restarting"
"$ROOT/deploy/nbaq" start serve >> "$LOG" 2>&1
log "restart result: $?"
