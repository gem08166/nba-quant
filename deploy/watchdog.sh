#!/usr/bin/env bash
# 看门狗：serve 或 watch 任一挂掉就自动拉起。由 cron 每 2 分钟调用。
#   serve 判断：pid 存活 + /api/health 可响应
#   watch 判断：pid 存活
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

if STATUS=$("$ROOT/deploy/nbaq" status 2>&1); then
  exit 0
fi

log "unhealthy: $(printf '%s' "$STATUS" | tr '\n' ';' | cut -c1-160)"
"$ROOT/deploy/nbaq" start >> "$LOG" 2>&1
log "restart result: $?"
