#!/usr/bin/env bash
# 源码同步：main 分支的 pull / push
#   deploy/gitsync.sh pull      拉取 GitHub 上的代码改动（公开仓，无需鉴权）
#   deploy/gitsync.sh push [msg] 把云主机上的改动推回 main（需 .env 的 GH_TOKEN）
#   deploy/gitsync.sh status    看本地与远端差异
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

load_token() {
  [[ -f .env ]] || { echo "缺少 .env"; exit 1; }
  TOKEN="$(grep '^GH_TOKEN=' .env | cut -d= -f2-)"
  [[ -n "$TOKEN" ]] || { echo ".env 里没有 GH_TOKEN"; exit 1; }
  REMOTE="https://x-access-token:${TOKEN}@github.com/gem08166/nba-quant.git"
}

case "${1:-status}" in
  pull)
    git fetch origin main
    if ! git diff --quiet HEAD origin/main; then
      git rebase origin/main
      echo "已同步到 $(git rev-parse --short HEAD)；改了代码记得 deploy/nbaq restart"
    else
      echo "已是最新：$(git rev-parse --short HEAD)"
    fi
    ;;
  push)
    load_token
    if [[ -z "$(git status --porcelain)" ]]; then
      echo "没有待提交的改动"
    else
      git add -A
      git commit -m "${2:-deploy: 云主机更新 $(date '+%F %T')}"
    fi
    git push "$REMOTE" main 2>&1 | grep -v "x-access-token" || true
    echo "已推送 main：$(git rev-parse --short HEAD)"
    ;;
  status)
    git fetch origin main --quiet || true
    echo "本地 HEAD: $(git rev-parse --short HEAD)  远端 main: $(git rev-parse --short origin/main 2>/dev/null || echo '?')"
    echo "待提交改动:"; git status --short | head -20
    ;;
  *) echo "用法: deploy/gitsync.sh {pull|push [msg]|status}"; exit 2 ;;
esac
