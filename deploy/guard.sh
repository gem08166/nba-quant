#!/usr/bin/env bash
# guard —— 常驻调度器（替代 cron 的角色，容器里 cron 守护进程会被回收）
#   每 60s：检查 serve / watch，挂了就拉起，异常推 ntfy
#   每 30 分钟：发布静态站到 gh-pages（Pages 构建上限 10 次/小时，30 分钟一档安全）
#   每 12 小时：全量刷新（ingest + 第二书源 + 训练 + 预测 + DB 快照）
#   每天 20 点后：推一条日报到 ntfy（三进程 / 线上时间 / 额度）
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
LOG="$ROOT/data/log/guard.log"
RUN="$ROOT/data/run"
PUB_MARK="$RUN/last_publish"
FULL_MARK="$RUN/last_full"
DIGEST_MARK="$RUN/last_digest"
PUB_INTERVAL=1800
FULL_INTERVAL=43200
ALERT_GAP=3600              # 同类告警最快 1 小时一次，避免刷屏
DIGEST_HOUR="${NBAQ_DIGEST_HOUR:-20}"
# 由 systemd 托管时（进程重启交给 Restart=always），健康巡检关掉，只留调度
GUARD_HEALTH="${NBAQ_GUARD_HEALTH:-1}"
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

topic() {
  awk -F= '/^NBAQ_NTFY_TOPIC=/{print $2}' "$ROOT/.env" 2>/dev/null \
    | tr -d '"' | tr -d "'" | tr -d ' \r'
}

# alert <标题> <正文> —— 失败告警，带 1 小时节流
alert() {
  local title="$1" body="$2" t
  t=$(topic)
  [[ -n "$t" ]] || return 0
  local now; now=$(date +%s)
  if [[ -n "${LAST_ALERT:-}" ]] && (( now - LAST_ALERT < ALERT_GAP )); then
    log "alert throttled: $title"
    return 0
  fi
  curl -fsS -m 12 -H "Title: $title" -H "Tags: warning" -H "Priority: high" \
    -d "$body" "https://ntfy.sh/$t" >/dev/null 2>&1 && LAST_ALERT=$now
  log "alert sent: $title"
}

# daily_digest —— 每天一次简报
daily_digest() {
  local t; t=$(topic)
  [[ -n "$t" ]] || return 0
  local procs gen rem used
  procs=$("$ROOT/deploy/nbaq" status 2>&1 | grep -E "^(serve|watch|guard) " | tr '\n' ' ')
  gen=$(curl -fsS -m 12 "https://gem08166.github.io/nba-quant/data/static.json" 2>/dev/null \
    | python3 -c "import sys,json;print(json.load(sys.stdin)['generated_at'])" 2>/dev/null || echo "拉取失败")
  rem=$(python3 -c "import json;print(json.load(open('$ROOT/data/odds_api_state.json'))['remaining'])" 2>/dev/null || echo "?")
  used=$(python3 -c "import json;print(json.load(open('$ROOT/data/odds_api_state.json'))['used'])" 2>/dev/null || echo "?")
  local body="进程: $procs
线上数据: $gen
Odds API: 剩余 $rem / 已用 $used"
  curl -fsS -m 12 -H "Title: nba-quant 日报" -H "Tags: chart_with_upwards_trend" \
    -d "$body" "https://ntfy.sh/$t" >/dev/null 2>&1
  log "digest sent"
}

log "guard started (pid $$) publish=${PUB_INTERVAL}s full=${FULL_INTERVAL}s health=${GUARD_HEALTH}"

while true; do
  # 1) 进程健康（systemd 托管时跳过，避免两套守护互相打架）
  if [[ "$GUARD_HEALTH" != "0" ]]; then
    if ! STATUS=$("$ROOT/deploy/nbaq" status 2>&1); then
      log "unhealthy: $(printf '%s' "$STATUS" | tr '\n' ';' | cut -c1-140)"
      "$ROOT/deploy/nbaq" start >> "$LOG" 2>&1
      rc_start=$?
      log "start rc=$rc_start"
      if [[ $rc_start -ne 0 ]]; then
        alert "nba-quant 服务拉不起来" "进程异常且自动重启失败，请查看 data/log/guard.log 与 serve.log。"
      fi
    fi
  fi

  # 2) 定时发布
  if [[ $(age_of "$PUB_MARK") -ge $PUB_INTERVAL ]]; then
    "$ROOT/deploy/publish.sh" >> "$LOG" 2>&1
    rc=$?
    touch "$PUB_MARK"
    log "publish rc=$rc"
    [[ $rc -ne 0 ]] && alert "nba-quant 发布失败" "publish.sh 返回 $rc，常见原因是 GH_TOKEN 失效（403）。日志：data/log/publish.log"
  fi

  # 3) 全量刷新
  if [[ $(age_of "$FULL_MARK") -ge $FULL_INTERVAL ]]; then
    "$ROOT/deploy/refresh.sh" full >> "$LOG" 2>&1
    rc=$?
    touch "$FULL_MARK"
    log "full refresh rc=$rc"
    [[ $rc -ne 0 ]] && alert "nba-quant 全量刷新失败" "refresh.sh full 返回 $rc，看 data/log/refresh.log。"
  fi

  # 4) 每天一次的日报（当天没发过，且已过设定小时）
  if [[ "${NBAQ_DIGEST:-1}" != "0" ]]; then
    TODAY=$(date '+%F')
    HOUR=$(date '+%H'); HOUR=$((10#$HOUR))
    if [[ $HOUR -ge $DIGEST_HOUR ]]; then
      if [[ "$(cat "$DIGEST_MARK" 2>/dev/null)" != "$TODAY" ]]; then
        daily_digest
        echo "$TODAY" > "$DIGEST_MARK"
      fi
    fi
  fi

  rotate "$LOG" 5242880
  sleep 60
done
