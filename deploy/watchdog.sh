#!/usr/bin/env bash
# 看门狗：serve 进程消失 / 端口不响应就自动拉起。由 cron 每 2 分钟调用。
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
LOG="$ROOT/data/log/watchdog.log"
mkdir -p "$ROOT/data/log"

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$1" >> "$LOG"; }

# 日志超过 10MB 时滚动，避免常年跑把磁盘写满
if [[ -f "$ROOT/data/log/serve.log" ]] && [[ $(stat -c%s "$ROOT/data/log/serve.log") -gt 10485760 ]]; then
  mv "$ROOT/data/log/serve.log" "$ROOT/data/log/serve.log.old"
  log "rotated serve.log"
fi

if "$ROOT/deploy/nbaq" status > /dev/null 2>&1; then
  exit 0
fi

log "serve down, restarting"
"$ROOT/deploy/nbaq" restart >> "$LOG" 2>&1
log "restart result: $?"
