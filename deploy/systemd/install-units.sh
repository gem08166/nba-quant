#!/usr/bin/env bash
# 在 systemd 主机上安装 nba-quant 三个 unit（serve / watch / guard）
#   sudo bash deploy/systemd/install-units.sh
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
UNIT_DIR=/etc/systemd/system
UNITS="nba-quant-serve nba-quant-watch nba-quant-guard"

if [[ $EUID -ne 0 ]]; then
  exec sudo -- bash "$0" "$@"
fi

OWNER="${SUDO_USER:-$(stat -c %U "$ROOT")}"
[[ "$OWNER" == "root" ]] && OWNER="${SUDO_USER:-$(basename "$ROOT")}"

echo "仓库: $ROOT"
echo "运行用户: $OWNER"

for u in $UNITS; do
  sed -e "s|__ROOT__|$ROOT|g" -e "s|__USER__|$OWNER|g" \
    "$ROOT/deploy/systemd/$u.service" > "$UNIT_DIR/$u.service"
  echo "  写入 $UNIT_DIR/$u.service"
done

# 旧 cron 方案在这台机器上会和新 unit 抢进程，清掉（保留其他用户的无关条目）
if crontab -l -u "$OWNER" 2>/dev/null | grep -q '# nba-quant'; then
  crontab -l -u "$OWNER" 2>/dev/null | grep -v '# nba-quant' | crontab -u "$OWNER" -
  echo "  已清理 $OWNER 的 nba-quant crontab 条目"
fi

systemctl daemon-reload
for u in $UNITS; do
  systemctl enable "$u" >/dev/null 2>&1 || true
done
systemctl restart nba-quant-serve nba-quant-watch nba-quant-guard

echo ""
systemctl --no-pager --lines=0 status nba-quant-serve nba-quant-watch nba-quant-guard || true
echo ""
echo "完成。查看日志：journalctl -u nba-quant-serve -f / journalctl -u nba-quant-watch -f"
echo "文件日志仍在：data/log/serve.log、data/watch.log、data/log/guard.log"
