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

echo "[4/6] 建库 + 训练 seed"
mkdir -p "$APP/data"
if [ ! -f "$APP/data/nba.db" ]; then
  .venv/bin/python -m app.cli init >/dev/null
  echo "  db initialized"
else
  echo "  db exists"
fi
# 空库没有历史完赛数据 → 评级模型训练不出来 → 哨兵静默 0 推送。
# seed_games.sql 由本机 `python -m scripts.make_seed` 生成（teams + 全部 games）。
if [ -f "$APP/deploy/seed_games.sql" ]; then
  .venv/bin/python - <<'PY'
import sqlite3
from pathlib import Path
sql = Path("deploy/seed_games.sql").read_text(encoding="utf-8")
conn = sqlite3.connect("data/nba.db")
conn.executescript(sql)
conn.commit()
finals = conn.execute(
    "SELECT COUNT(*) FROM games WHERE status='STATUS_FINAL' AND home_score IS NOT NULL"
).fetchone()[0]
print(f"  seed imported: finals={finals}")
if finals < 30:
    raise SystemExit("seed too small: ratings model needs >=30 completed games")
PY
else
  echo "  WARN: deploy/seed_games.sql 缺失，评级模型会训练失败（哨兵将 0 推送）"
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
