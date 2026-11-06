"""把历史赛前信号回填成纸面注单（研究用）。

背景：纸面台账从 2026-11 才开始记，样本太少没法评估「信号值不值得跟」。
这个脚本把历史 prediction 里**真正触发过的信号**（signal=True）按当时能拿到的
赔率回填成注单，并用终局比分结算，快速扩展样本。

口径（与 signal_backtest 一致，保证可复现）：
- 每个 (game, market, side, line) 只取**首次出现**的那条信号（模拟当时下注）
- 入场价用信号生成时刻该场该市场的实际报价；拿不到时退回信号自带价格
- 结算用终局比分（spread/total 走盘记 push）
- 盈亏：胜 = 注额 × (赔率 − 1)；负 = −注额；走 = 0
- CLV 与线移动按赛前收盘价计算（能取到才算）

**这些是回顾性注单，不是当时真实决策**：备注统一以「回填」开头，
收益页会单独分组，不会污染真实记录的统计。

用法：
    python -m scripts.backfill_bets [--stake 1] [--start 2026-10-20] [--end 2026-11-06] [--dry-run]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.jobs.signal_backtest import _closing_price, _entry_snapshot, settle_edge


def backfill(conn, stake: float = 1.0, start: str | None = None, end: str | None = None,
             dry_run: bool = False) -> dict:
    rows = conn.execute(
        """
        SELECT p.game_id, p.created_at, p.details, g.slate_date, g.tipoff,
               g.home_score, g.away_score,
               a.abbrev AS away_abbrev, h.abbrev AS home_abbrev
        FROM predictions p
        JOIN games g ON g.id = p.game_id
        JOIN teams h ON h.id = g.home_id
        JOIN teams a ON a.id = g.away_id
        WHERE g.home_score IS NOT NULL AND g.away_score IS NOT NULL
        ORDER BY p.id
        """
    ).fetchall()

    # 已存在的注单（避免重复回填）
    existing = {
        (str(r["game_id"]), r["market"], r["side"], r["line"])
        for r in conn.execute("SELECT game_id, market, side, line FROM bets")
    }

    seen: set[tuple] = set()
    inserted = 0
    skipped_dupe = 0
    skipped_no_price = 0
    stats = {"won": 0, "lost": 0, "push": 0}
    stake_sum = 0.0
    profit_sum = 0.0

    for row in rows:
        slate = row["slate_date"]
        if start and slate < start:
            continue
        if end and slate > end:
            continue
        try:
            details = json.loads(row["details"])
        except (TypeError, ValueError):
            continue
        for edge in details.get("edges") or []:
            if not edge.get("signal"):
                continue
            key = (row["game_id"], edge["market"], edge["side"], edge.get("line"))
            if key in seen:
                continue  # 同一信号只回填首次出现
            seen.add(key)
            if key in existing:
                skipped_dupe += 1
                continue

            entry = _entry_snapshot(
                conn, row["game_id"], edge["market"], edge["side"],
                edge.get("line"), row["created_at"],
            )
            price = float(entry["price"]) if entry and entry.get("price") else None
            if not price:
                price = float(edge.get("price") or 0) or None
            if not price or price <= 1.0:
                skipped_no_price += 1
                continue
            line = entry["line"] if entry else edge.get("line")

            result = settle_edge(edge["market"], edge["side"], line, row["home_score"], row["away_score"])
            if result == "won":
                profit = stake * (price - 1.0)
            elif result == "lost":
                profit = -stake
            else:
                profit = 0.0

            closing = None
            try:
                closing = _closing_price(
                    conn, row["game_id"], edge["market"], edge["side"], line, row["tipoff"]
                )
            except Exception:  # noqa: BLE001
                closing = None
            clv = (price / closing - 1.0) if closing else None

            ev = edge.get("ev")
            note = (
                f"回填 赛前信号 · {edge.get('book') or '—'} · "
                f"EV {ev:+.1%} · {slate}" if isinstance(ev, (int, float)) else
                f"回填 赛前信号 · {edge.get('book') or '—'} · {slate}"
            )

            stats[result] = stats.get(result, 0) + 1
            stake_sum += stake
            profit_sum += profit
            inserted += 1

            if not dry_run:
                conn.execute(
                    """
                    INSERT INTO bets(game_id, market, side, line, price, stake,
                                    model_prob, ev, clv, placed_at, note, result, profit)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        row["game_id"],
                        edge["market"],
                        edge["side"],
                        line,
                        price,
                        stake,
                        edge.get("model_prob") if edge.get("model_prob") is not None else 0.5,
                        ev if isinstance(ev, (int, float)) else 0.0,
                        clv,
                        row["created_at"] or datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        note,
                        result,
                        round(profit, 4),
                    ),
                )
        if not dry_run:
            conn.commit()
    if not dry_run:
        conn.commit()

    return {
        "inserted": inserted,
        "skipped_dupe": skipped_dupe,
        "skipped_no_price": skipped_no_price,
        "wins": stats.get("won", 0),
        "losses": stats.get("lost", 0),
        "pushes": stats.get("push", 0),
        "stake": round(stake_sum, 2),
        "profit": round(profit_sum, 2),
        "roi": round(profit_sum / stake_sum, 4) if stake_sum else None,
        "dry_run": dry_run,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="回填历史赛前信号为纸面注单")
    parser.add_argument("--stake", type=float, default=1.0, help="每条注额（默认 1）")
    parser.add_argument("--start", default=None, help="起始比赛日 YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="结束比赛日 YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    args = parser.parse_args()

    conn = db.connect()
    try:
        stats = backfill(conn, stake=args.stake, start=args.start, end=args.end, dry_run=args.dry_run)
    finally:
        conn.close()

    print(
        ("[dry-run] " if stats["dry_run"] else "") +
        f"回填 {stats['inserted']} 条 · 胜{stats['wins']}/负{stats['losses']}/走{stats['pushes']} · "
        f"投入 {stats['stake']:.2f} · 盈亏 {stats['profit']:+.2f} · "
        f"ROI {(stats['roi'] * 100 if stats['roi'] is not None else 0):+.1f}%"
    )
    if stats["skipped_dupe"] or stats["skipped_no_price"]:
        print(f"  跳过：已存在 {stats['skipped_dupe']} · 无有效入场价 {stats['skipped_no_price']}")


if __name__ == "__main__":
    main()
