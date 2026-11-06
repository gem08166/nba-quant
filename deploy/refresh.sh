#!/usr/bin/env bash
# 数据刷新：拉盘口 → 训练 → 预测 → 收盘价 → 结算。由 cron 定时调用。
#   deploy/refresh.sh live   每 30 分钟：watch --once（轻量，抓价+收盘+结算+预测+推送）
#   deploy/refresh.sh full   每天两次：全量 ingest + 第二书源 + train + predict + 报告
set -uo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PY="$ROOT/.venv/bin/python"
MODE="${1:-live}"
LOG_DIR="$ROOT/data/log"
LOG="$LOG_DIR/refresh.log"
LOCK="$ROOT/data/run/refresh.lock"
mkdir -p "$LOG_DIR" "$ROOT/data/run"

exec 9> "$LOCK"
if ! flock -n 9; then
  echo "[$(date '+%F %T')] another refresh is running, skip $MODE" >> "$LOG"
  exit 0
fi

cd "$ROOT"
{
  echo ""
  echo "===== $(date '+%F %T') refresh $MODE ====="
  SLATE=$("$PY" -c 'from app.dates import current_slate_date; print(current_slate_date())')
  echo "slate=$SLATE"

  if [[ "$MODE" == "full" ]]; then
    "$PY" -m app.cli ingest --date "$SLATE" || echo "ingest failed"
    "$PY" -m app.cli hao-odds --date "$SLATE" || true
    "$PY" -m app.cli odds-api --date "$SLATE" || true
    "$PY" -m app.cli hoops-sync || true
    "$PY" -m app.cli train || echo "train failed"
    "$PY" -m app.cli predict --date "$SLATE" || echo "predict failed"
    "$PY" -m app.cli closing || true
    "$PY" -m app.cli settle || true
  else
    "$PY" -m app.cli watch --once || echo "watch tick failed"
  fi

  echo "===== $(date '+%F %T') done ====="
} >> "$LOG" 2>&1

# 每轮刷新后把结果发到 GitHub Pages（云主机是唯一数据源）
{
  echo "----- $(date '+%F %T') publish -----"
  if [[ "$MODE" == "full" ]]; then
    "$ROOT/deploy/publish.sh" --with-db || echo "publish failed"
    touch "$ROOT/data/run/last_publish"
  else
    "$ROOT/deploy/publish.sh" || echo "publish failed"
    touch "$ROOT/data/run/last_publish"
  fi
} >> "$LOG" 2>&1
