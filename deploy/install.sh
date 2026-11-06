#!/usr/bin/env bash
# 一键安装：依赖 → 数据库 → 定时任务 → 启动服务。可重复执行（幂等）。
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PY="$ROOT/.venv/bin/python"
cd "$ROOT"

echo "[1/5] 检查 Python"
PY3=$(command -v python3.12 || command -v python3.11 || command -v python3)
"$PY3" -c 'import sys; assert sys.version_info >= (3, 11), sys.version' || {
  echo "需要 Python >= 3.11" >&2; exit 1; }

echo "[2/5] 创建虚拟环境并安装依赖"
[[ -d "$ROOT/.venv" ]] || "$PY3" -m venv "$ROOT/.venv"
"$PY" -m pip install -q --upgrade pip
"$PY" -m pip install -q -e ".[dev]"

echo "[3/5] 准备数据库"
mkdir -p "$ROOT/data"
if [[ ! -s "$ROOT/data/nba.db" ]]; then
  if git -C "$ROOT" fetch origin data:refs/remotes/origin/data --depth=1 2>/dev/null; then
    git -C "$ROOT" show origin/data:nba.db > "$ROOT/data/nba.db"
    echo "  已从 data 分支恢复 data/nba.db"
  else
    echo "  没有 data 分支快照，创建空库后需要先回填历史"
  fi
fi
"$PY" -m app.cli init
"$PY" -m app.cli status

echo "[4/5] 安装定时任务（每 2 分钟看门狗 / 每 30 分钟轻量刷新 / 每天两次全量）"
chmod +x "$ROOT"/deploy/nbaq "$ROOT"/deploy/watchdog.sh "$ROOT"/deploy/refresh.sh
NEW_CRON=$(crontab -l 2>/dev/null | grep -v '# nba-quant' || true)
NEW_CRON="${NEW_CRON}
@reboot $ROOT/deploy/nbaq start >/dev/null 2>&1 # nba-quant
*/2 * * * * $ROOT/deploy/watchdog.sh >/dev/null 2>&1 # nba-quant
*/30 * * * * $ROOT/deploy/refresh.sh live >/dev/null 2>&1 # nba-quant
30 10,18 * * * $ROOT/deploy/refresh.sh full >/dev/null 2>&1 # nba-quant"
printf '%s\n' "$NEW_CRON" | crontab -
echo "  当前定时任务："
crontab -l | grep '# nba-quant' | sed 's/^/    /'

echo "[5/5] 启动服务"
"$ROOT/deploy/nbaq" restart

echo ""
echo "完成。访问：http://<本机IP>:${NBAQ_PORT:-8000}"
echo "日志：$ROOT/data/log/serve.log    刷新：$ROOT/data/log/refresh.log"
