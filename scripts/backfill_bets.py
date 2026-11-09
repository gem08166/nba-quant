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


def _select_signals(rows, strict: bool, bettable_only: bool = False):
    """从历史预测里挑出「当时会真的下注」的信号。

    strict（默认）：
    - 同一市场只取**首次出现**的那一条（盘口漂移 232.5/232.0 视为同一决策）
    - 同场让分与胜负二选一（保留 EV 更高的那条），与 predict.MUTUAL_EXCLUSIVE 一致
    → 每场最多 2 条（让分/胜负 + 大小）

    raw：
    - 按 (market, side, line) 各取首次出现（旧行为，会有同场相关仓位）

    bettable_only（可下注口径）：
    - 只取 bettable=True 的边；同一 (game, market, side, line) 取**首个通过阀门的**
      版本（模拟"当时阀门放行才下注"）；
    - 11-04 闸门上线前的旧预测没有 bettable 字段，按"阀门未存在"处理：全部排除，
      不做假设（无法判定就不假装能判定）。
    """
    chosen: dict[tuple, dict] = {}
    for row in rows:
        try:
            details = json.loads(row["details"])
        except (TypeError, ValueError):
            continue
        for edge in details.get("edges") or []:
            if not edge.get("signal"):
                continue
            if bettable_only:
                # 必须是阀门明确放行的版本；无字段的旧版本不算
                if edge.get("bettable") is not True:
                    continue
            key = (row["game_id"], edge["market"]) if strict else (
                row["game_id"], edge["market"], edge["side"], edge.get("line")
            )
            if key in chosen:
                continue  # 首次出现优先
            chosen[key] = {"row": row, "edge": edge}

    items = list(chosen.values())
    if strict:
        # 同场 spread / moneyline 高度相关：只留 EV 更高的
        by_game: dict[str, list[dict]] = {}
        for item in items:
            by_game.setdefault(item["row"]["game_id"], []).append(item)
        kept: list[dict] = []
        for game_items in by_game.values():
            corr = [i for i in game_items if i["edge"]["market"] in ("spread", "moneyline")]
            if len(corr) > 1:
                corr.sort(key=lambda i: i["edge"].get("ev") or 0, reverse=True)
                drop = {id(i) for i in corr[1:]}
                kept.extend(i for i in game_items if id(i) not in drop)
            else:
                kept.extend(game_items)
        items = kept
    return items


def backfill(conn, stake: float = 1.0, start: str | None = None, end: str | None = None,
             dry_run: bool = False, strict: bool = True, replace: bool = False,
             bettable_only: bool = False, sim_only: bool = False,
             tombstones: set[str] | None = None) -> dict:
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

    # 只保留日期区间内的比赛
    rows = [
        r for r in rows
        if (not start or r["slate_date"] >= start) and (not end or r["slate_date"] <= end)
    ]

    replaced = 0
    if replace and not dry_run:
        replaced = conn.execute("DELETE FROM bets WHERE note LIKE '回填%'").rowcount or 0
        conn.commit()

    # 墓碑：被用户清空/删除的模拟信号不要再自动补回来
    if sim_only and tombstones is None:
        try:
            from app.jobs import betssync

            tombstones = betssync.load_deletions()
        except Exception:  # noqa: BLE001
            tombstones = set()
    tombstones = tombstones or set()
    sim_ref = None
    if sim_only:
        from app.jobs.simulate import sim_ref as _sim_ref

        sim_ref = _sim_ref

    # 已存在的注单（避免重复回填）。sim_only：只把模拟注单当已有，
    # 真实注单不挡模拟样本（否则「跟着信号下过」的信号会从模拟统计里消失）。
    if sim_only:
        existing = {
            (str(r["game_id"]), r["market"], r["side"], r["line"])
            for r in conn.execute(
                "SELECT game_id, market, side, line FROM bets "
                "WHERE note LIKE '回填%' OR note LIKE '模拟%'"
            )
        }
    else:
        existing = {
            (str(r["game_id"]), r["market"], r["side"], r["line"])
            for r in conn.execute("SELECT game_id, market, side, line FROM bets")
        }

    items = _select_signals(rows, strict, bettable_only=bettable_only)

    inserted = 0
    skipped_dupe = 0
    skipped_no_price = 0
    skipped_tombstone = 0
    stats = {"won": 0, "lost": 0, "push": 0}
    stake_sum = 0.0
    profit_sum = 0.0

    for item in items:
        row, edge = item["row"], item["edge"]
        slate = row["slate_date"]
        key = (row["game_id"], edge["market"], edge["side"], edge.get("line"))
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

        ref = None
        if sim_only and sim_ref is not None:
            ref = sim_ref(row["game_id"], edge["market"], edge["side"], line)
            if ref in tombstones:
                skipped_tombstone += 1
                continue

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
        if sim_only:
            note = (
                f"模拟 可下注 · {edge.get('book') or '—'} · EV {ev:+.1%} · {slate}"
                if isinstance(ev, (int, float))
                else f"模拟 可下注 · {edge.get('book') or '—'} · {slate}"
            )
        elif bettable_only:
            note = f"回填 可下注 · {edge.get('book') or '—'} · EV {ev:+.1%} · {slate}" \
                if isinstance(ev, (int, float)) else f"回填 可下注 · {edge.get('book') or '—'} · {slate}"

        stats[result] = stats.get(result, 0) + 1
        stake_sum += stake
        profit_sum += profit
        inserted += 1

        if not dry_run:
            cur = conn.execute(
                """
                INSERT INTO bets(game_id, market, side, line, price, stake,
                                model_prob, ev, clv, placed_at, note, result, profit, ref)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ref,
                ),
            )
            # 非模拟口径的回填：补一个稳定 ref（否则云端合回只能靠自然键，唯一索引留空）
            if ref is None:
                conn.execute("UPDATE bets SET ref = 'srv-' || id WHERE id = ?", (cur.lastrowid,))
            existing.add(key)
    if not dry_run:
        conn.commit()

    return {
        "inserted": inserted,
        "skipped_dupe": skipped_dupe,
        "skipped_no_price": skipped_no_price,
        "skipped_tombstone": skipped_tombstone,
        "replaced": replaced,
        "wins": stats.get("won", 0),
        "losses": stats.get("lost", 0),
        "pushes": stats.get("push", 0),
        "stake": round(stake_sum, 2),
        "profit": round(profit_sum, 2),
        "roi": round(profit_sum / stake_sum, 4) if stake_sum else None,
        "dry_run": dry_run,
        "strict": strict,
        "bettable_only": bettable_only,
        "sim_only": sim_only,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="回填历史赛前信号为纸面注单")
    parser.add_argument("--stake", type=float, default=1.0, help="每条注额（默认 1）")
    parser.add_argument("--start", default=None, help="起始比赛日 YYYY-MM-DD")
    parser.add_argument("--end", default=None, help="结束比赛日 YYYY-MM-DD")
    parser.add_argument("--dry-run", action="store_true", help="只统计不写库")
    parser.add_argument(
        "--raw",
        action="store_true",
        help="旧口径：按每个 (market, side, line) 回填（会产生同场相关仓位）",
    )
    parser.add_argument(
        "--replace",
        action="store_true",
        help="先删除已有的回填注单再重填（只删备注以「回填」开头的）",
    )
    parser.add_argument(
        "--bettable-only",
        action="store_true",
        help="只回填通过可下注阀门（bettable=True）的信号；11-04 闸门上线前的旧信号排除",
    )
    args = parser.parse_args()

    conn = db.connect()
    try:
        stats = backfill(
            conn,
            stake=args.stake,
            start=args.start,
            end=args.end,
            dry_run=args.dry_run,
            strict=not args.raw,
            replace=args.replace,
            bettable_only=args.bettable_only,
        )
    finally:
        conn.close()

    print(
        ("[dry-run] " if stats["dry_run"] else "") +
        f"回填 {stats['inserted']} 条"
        + (f"（已替换旧回填 {stats['replaced']} 条）" if stats["replaced"] else "")
        + ("（仅可下注）" if stats["bettable_only"] else "")
        + f" · 胜{stats['wins']}/负{stats['losses']}/走{stats['pushes']} · "
        f"投入 {stats['stake']:.2f} · 盈亏 {stats['profit']:+.2f} · "
        f"ROI {(stats['roi'] * 100 if stats['roi'] is not None else 0):+.1f}%"
        + ("" if stats["strict"] else "  [raw 口径]")
    )
    if stats["skipped_dupe"] or stats["skipped_no_price"]:
        print(f"  跳过：已存在 {stats['skipped_dupe']} · 无有效入场价 {stats['skipped_no_price']}")


if __name__ == "__main__":
    main()
