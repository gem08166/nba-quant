#!/usr/bin/env bash
# 把云主机算好的结果发布到 GitHub Pages。
#
# 做的事：
#   1. scripts/export_static 导出静态站到 site/
#   2. site/ 内容 force-push 到 gh-pages 分支（单 commit，历史不增长）
#   3. （可选）data/nba.db 快照 force-push 到 data 分支，供灾备/回滚
#
# 需要 .env 里的 GH_TOKEN：classic PAT（repo + workflow）或 fine-grained
# （Contents: RW / Actions: RW / Pages: RW / Workflows: RW）。
#
# 用法：deploy/publish.sh [--with-db]
set -euo pipefail

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PY="$ROOT/.venv/bin/python"
LOG_DIR="$ROOT/data/log"
LOG="$LOG_DIR/publish.log"
LOCK="$ROOT/data/run/publish.lock"
TMP="$ROOT/data/run/publish-work"
mkdir -p "$LOG_DIR" "$ROOT/data/run"

exec 9> "$LOCK"
flock -n 9 || { echo "[$(date '+%F %T')] publish already running, skip" >> "$LOG"; exit 0; }

log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*" >> "$LOG"; }

TOKEN=$(grep -E '^GH_TOKEN=' "$ROOT/.env" 2>/dev/null | cut -d= -f2- | tr -d '"' || true)
if [[ -z "${TOKEN:-}" ]]; then
  log "ERROR: .env 缺少 GH_TOKEN，无法发布"
  echo "缺少 GH_TOKEN，见 deploy/README.md 的发布章节" >&2
  exit 1
fi

REMOTE=$(git -C "$ROOT" remote get-url origin)
REPO_PATH=$(printf '%s' "$REMOTE" | sed -E 's#^(https://github.com/|git@github.com:)##; s#\.git$##')
log "repo=$REPO_PATH"

cd "$ROOT"

# 1) 导出静态站
"$PY" -m scripts.export_static --out "$ROOT/site" >> "$LOG" 2>&1 || { log "ERROR: export_static failed"; exit 1; }

# 2) gh-pages 单 commit force push
rm -rf "$TMP"
mkdir -p "$TMP"
cp -a "$ROOT/site/." "$TMP/"
cd "$TMP"
git init -q
git checkout -q -b gh-pages
git add -A
git -c user.name="nba-quant-bot" -c user.email="nba-quant@users.noreply.github.com" \
    commit -q -m "publish: $(date -u '+%F %T')Z"
git push -q --force "https://x-access-token:${TOKEN}@github.com/${REPO_PATH}.git" gh-pages:gh-pages
log "pushed gh-pages ($(find . -type f | wc -l) files)"

# 3) 可选：数据库快照
if [[ "${1:-}" == "--with-db" ]]; then
  "$PY" -c "import sqlite3; c=sqlite3.connect('$ROOT/data/nba.db'); c.execute('PRAGMA wal_checkpoint(TRUNCATE)'); c.close()"
  rm -rf "$TMP-db"; mkdir -p "$TMP-db"
  cp "$ROOT/data/nba.db" "$TMP-db/nba.db"
  cd "$TMP-db"
  git init -q
  git checkout -q -b data
  git add nba.db
  git -c user.name="nba-quant-bot" -c user.email="nba-quant@users.noreply.github.com" \
      commit -q -m "db snapshot: $(date -u '+%F %T')Z"
  git push -q --force "https://x-access-token:${TOKEN}@github.com/${REPO_PATH}.git" data:data
  log "pushed data branch ($(du -h nba.db | cut -f1))"
fi

log "publish done"
echo "published"
