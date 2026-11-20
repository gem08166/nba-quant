#!/usr/bin/env bash
# NBA Quant 夜间哨兵 · VPS 安装（Debian 13 / 幂等，可重复执行）
set -euo pipefail

APP=/opt/nba-quant
ENVDIR=/etc/nbaq

echo "[1/6] 目录与凭据"
install -d -m 700 "$ENVDIR"
if [ -f /tmp/sentinel.env ]; then
  install -m 600 /tmp/sentinel.env "$ENVDIR/sentinel.env"
  rm -f /tmp/sentinel.env
  echo "  env installed"
else
  echo "  env already present (skip)"
fi

echo "[2/6] 代码目录"
test -d "$APP/app" || { echo "missing $APP/app"; exit 1; }

echo "[3/6] venv + 依赖"
cd "$APP"
if [ ! -x .venv/bin/python ]; then
  python3 -m venv .venv
fi
.venv/bin/pip install -q -U pip
.venv/bin/pip install -q httpx numpy python-dotenv tzdata
.venv/bin/python - <<'PY'
import httpx, numpy, dotenv
from zoneinfo import ZoneInfo
from datetime import datetime
print("  deps ok: numpy", numpy.__version__, "| NY time", datetime.now(ZoneInfo("America/New_York")).strftime("%Y-%m-%d %H:%M"))
PY

echo "[4/6] 建库"
mkdir -p "$APP/data"
if [ ! -f "$APP/data/nba.db" ]; then
  .venv/bin/python -m app.cli init >/dev/null
  echo "  db initialized"
else
  echo "  db exists"
fi

echo "[5/6] systemd"
install -m 644 "$APP/deploy/nbaq-sentinel.service" /etc/systemd/system/nbaq-sentinel.service
systemctl daemon-reload
systemctl enable nbaq-sentinel >/dev/null
systemctl restart nbaq-sentinel
sleep 3
systemctl is-active nbaq-sentinel

echo "[6/6] 自检（单轮，导出到本地文件）"
set -a; . "$ENVDIR/sentinel.env"; set +a
.venv/bin/python -m scripts.sentinel --once --export-file /tmp/sentinel-check.json 2>&1 | tail -8
ls -lh /tmp/sentinel-check.json || true
echo "done"
