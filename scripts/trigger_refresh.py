"""用 workflow_dispatch 触发一轮轻量刷新（绕过 GitHub 对 schedule 的限流）。

背景：公开仓库的 `on: schedule` 会被 GitHub 节流，`*/10` 实测也只跑约
60 分钟一轮；`workflow_dispatch` 是即时排队，不受这个限制。本机 watch 在
赛时每 N 分钟调一次，Pages / 坚果云的静态快照就能保持 10 分钟级新鲜。

令牌优先级：
  1. 环境变量 NBAQ_GH_TOKEN
  2. .env 里的 NBAQ_GH_TOKEN
（只用 classic PAT 的 repo 权限 / fine-grained 的 actions:write，不需要其他权限。）

用法：
    python -m scripts.trigger_refresh                # 触发 odds-refresh.yml
    python -m scripts.trigger_refresh --dry-run      # 只看有没有令牌
    python -m scripts.trigger_refresh --workflow refresh.yml
"""
from __future__ import annotations

import argparse
import os
import sys

import httpx
from dotenv import load_dotenv

from app.config import ROOT

load_dotenv(ROOT / ".env")

REPO = os.getenv("NBAQ_GH_REPO", "gem08166/nba-quant")
DEFAULT_WORKFLOW = os.getenv("NBAQ_GH_WORKFLOW", "odds-refresh.yml")


def token() -> str:
    return (os.getenv("NBAQ_GH_TOKEN") or "").strip()


def trigger_workflow(workflow: str = DEFAULT_WORKFLOW, ref: str = "main") -> dict:
    """触发一次 workflow_dispatch。

    返回 {"ok": bool, "status": int, "message": str}；不抛异常（watch 里每轮调用，
    网络抖动不该中断采样循环）。
    """
    tok = token()
    if not tok:
        return {"ok": False, "status": 0, "message": "未配置 NBAQ_GH_TOKEN"}

    url = f"https://api.github.com/repos/{REPO}/actions/workflows/{workflow}/dispatches"
    try:
        resp = httpx.post(
            url,
            headers={
                "Authorization": f"Bearer {tok}",
                "Accept": "application/vnd.github+json",
                "User-Agent": "nbaq-local-trigger",
            },
            json={"ref": ref},
            timeout=20,
        )
    except httpx.HTTPError as exc:
        return {"ok": False, "status": 0, "message": f"网络错误：{exc}"}

    if resp.status_code == 204:
        return {"ok": True, "status": 204, "message": f"已触发 {workflow}"}
    detail = resp.text[:200].replace("\n", " ")
    if resp.status_code in (401, 403):
        detail = "令牌无效或权限不足（需要 actions:write / repo）"
    elif resp.status_code == 404:
        detail = "仓库或 workflow 不存在（检查 NBAQ_GH_REPO / 文件名）"
    return {"ok": False, "status": resp.status_code, "message": detail}


def main() -> None:
    parser = argparse.ArgumentParser(description="触发 GitHub Actions 轻量刷新")
    parser.add_argument("--workflow", default=DEFAULT_WORKFLOW)
    parser.add_argument("--ref", default="main")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if args.dry_run:
        print(f"token: {'已配置' if token() else '缺失'} · repo={REPO} · workflow={args.workflow}")
        return

    result = trigger_workflow(args.workflow, args.ref)
    print(result["message"])
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
