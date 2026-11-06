#!/usr/bin/env bash
# guard —— 常驻调度器（替代 cron 的角色，容器里 cron 守护进程会被回收）
#   每 60s：检查 serve / watch，挂了就拉起
#   每 30 分钟：发布静态站到 gh-pages（Pages 构建上限 10 次/小时，30 分钟一档安全）
#   每 12 小时：全量刷新（ingest + 第二书源 + 训练 + 预测 + DB 快照）
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
LOG="$ROOT/data/log/guard.log"
RUN="$ROOT/data/run"
PUB_MARK="$RUN/last_publish"
FULL_MARK="$RUN/last_full"
PUB_INTERVAL=1800
FULL_INTERVAL=43200
mkdir -p "$RUN" "$ROOT/data/log"

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$1" >> "$LOG"; }

age_of() {
  if [[ -f "$1" ]]; then echo $(( $(date +%s) - $(stat -c %Y "$1") )); else echo 999999; fi
}

rotate() {
  local f="$1" limit="$2"
  [[ -f "$f" ]] || return 0
  [[ $(stat -c%s "$f") -gt "$limit" ]] || return 0
  mv "$f" "$f.old"
  log "rotated $(basename "$f")"
}

log "guard started (pid $$) publish=${PUB_INTERVAL}s full=${FULL_INTERVAL}s"

while true; do
  # 1) 进程健康
  if ! STATUS=$("$ROOT/deploy/nbaq" status 2>&1); then
    log "unhealthy: $(printf '%s' "$STATUS" | tr '\n' ';' | cut -c1-140)"
    "$ROOT/deploy/nbaq" start >> "$LOG" 2>&1
    log "start rc=$?"
  fi

  # 2) 定时发布
  if [[ $(age_of "$PUB_MARK") -ge $PUB_INTERVAL ]]; then
    "$ROOT/deploy/publish.sh" >> "$LOG" 2>&1
    rc=$?
    touch "$PUB_MARK"
    log "publish rc=$rc"
  fi

  # 3) 全量刷新
  if [[ $(age_of "$FULL_MARK") -ge $FULL_INTERVAL ]]; then
    "$ROOT/deploy/refresh.sh" full >> "$LOG" 2>&1
    rc=$?
    touch "$FULL_MARK"
    log "full refresh rc=$rc"
  fi

  rotate "$LOG" 5242880
  sleep 60
done
