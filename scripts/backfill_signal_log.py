"""把历史 predictions.details 里的信号回填进 signal_log（一次性，可重跑）。

signal_log 从 2026-11-13 起才会在 predict 时自动写入；更早的信号仍完整地存在
`predictions.details` 的 edges 里。本脚本按 created_at 升序重放，保证「首见即记」
与线上行为一致，然后调用 settle_signal_log 回填收盘价与结果。

用法：
    python -m scripts.backfill_signal_log [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.jobs.predict import log_signals
from app.jobs.settle import settle_signal_log


def backfill(conn, dry_run: bool = False) -> dict:
    rows = conn.execute(
        """
        SELECT p.game_id, p.created_at, p.details, g.slate_date
        FROM predictions p JOIN games g ON g.id = p.game_id
        ORDER BY p.created_at, p.id
        """
    ).fetchall()

    inserted = games = signals = 0
    for row in rows:
        try:
            details = json.loads(row["details"] or "{}")
        except (TypeError, ValueError):
            continue
        edges = details.get("edges") or []
        if not any(e.get("signal") for e in edges):
            continue
        games += 1
        signals += sum(1 for e in edges if e.get("signal"))
        if dry_run:
            continue
        inserted += log_signals(
            conn, row["game_id"], row["slate_date"], edges, row["created_at"]
        )
    if not dry_run:
        conn.commit()
        settled = settle_signal_log(conn)
    else:
        settled = 0
    return {
        "prediction_rows": len(rows),
        "games_with_signals": games,
        "signal_edges": signals,
        "inserted": inserted,
        "settled": settled,
        "dry_run": dry_run,
    }


def main() -> None:
    ap = argparse.ArgumentParser(description="回填 signal_log")
    ap.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = ap.parse_args()

    conn = db.connect()
    try:
        stats = backfill(conn, dry_run=args.dry_run)
    finally:
        conn.close()
    print(
        ("[dry-run] " if stats["dry_run"] else "")
        + f"预测行 {stats['prediction_rows']} · 有信号比赛 {stats['games_with_signals']} · "
        f"信号边 {stats['signal_edges']} · 入库 {stats['inserted']} · 已结算 {stats['settled']}"
    )


if __name__ == "__main__":
    main()
