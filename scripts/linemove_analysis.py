"""信号 → 线移动 方向检验（第 0 层验收：模型信号是否预测线移动）。

这是"往可下注方向走"的第一道闸门，比 ROI 检验便宜得多：
- ROI 检验要等几百注才能区分运气；
- 线移动检验在每条信号出现后 N 小时就能给出一个观察。

方法：
- 对每条历史信号（predictions.details.edges 中 signal=true 的边），
  找到信号时刻该市场该侧的最优可得价（与 signal_backtest._entry_snapshot 同口径）；
  再取收盘时刻（开赛前最后一笔）的线。
- 线移动方向 = 收盘线 − 信号线，按"对信号有利"定向：
  spread home：收盘线更负（-5.5 → -6.5）= 利好 home 侧 = +1
  total over：收盘线更高（220.5 → 221.5）= 利好 over = +1
- 若信号侧的线朝信号方向移动的比例显著 > 50%，说明模型信号包含市场尚未
  定价的信息（beat the close 的必要条件）。

样本来源：odds_snapshots 的实时积累（watch / Actions 每日两次）。
SBR 收盘回填只有单时点，不参与线移动检验。

用法：python -m scripts.linemove_analysis [--hours 6] [--start 2026-11-01]
输出：data/linemove_report.json + 终端摘要
"""
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone

from app import db
from app.config import DATA_DIR
from app.jobs.signal_backtest import _entry_snapshot

HORIZON_HOURS = float(__import__("os").getenv("NBAQ_LINEMOVE_HORIZON", "6"))


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _line_moves_toward(market: str, side: str, from_line: float, to_line: float) -> int:
    """+1 = 线朝信号方向移动；-1 = 反向；0 = 不动。
    约定：spread 存 home 线（越负主队越强）；total 存 over 线（越高大分越多）。"""
    if market == "spread":
        delta = to_line - from_line
        return int((delta < 0) - (delta > 0)) if side == "home" else int((delta > 0) - (delta < 0))
    if market == "total":
        delta = to_line - from_line
        return int((delta > 0) - (delta < 0)) if side == "over" else int((delta < 0) - (delta > 0))
    return 0


def _closing_line(
    conn, game_id: str, market: str, side: str, line: float | None, tipoff: str | None
) -> dict | None:
    """开赛前最后一笔同市场同侧快照（任意线）——线移动=信号线 vs 收盘线。

    注意不能只看"信号线上的最后一笔"：那永远等于信号线，方向恒为 0，
    检验就失效了。线完全翻越（收盘时该侧已是另一条线）时返回那条新线。"""
    tip = _parse_ts(tipoff)
    rows = conn.execute(
        """
        SELECT line, price, captured_at FROM odds_snapshots
        WHERE game_id=? AND market=? AND side=? AND line IS NOT NULL
        ORDER BY id
        """,
        (game_id, market, side),
    ).fetchall()
    candidates = [
        row
        for row in rows
        if (tip is None or (_parse_ts(row["captured_at"]) or tip) <= tip)
    ]
    return dict(candidates[-1]) if candidates else None


def run(start: str | None = None, horizon_hours: float | None = None) -> dict:
    horizon = horizon_hours if horizon_hours is not None else HORIZON_HOURS
    conn = db.connect()
    try:
        rows = conn.execute(
            """
            SELECT p.game_id, p.created_at, p.details, g.slate_date, g.tipoff
            FROM predictions p JOIN games g ON g.id = p.game_id
            ORDER BY p.id
            """
        ).fetchall()

        observations: list[dict] = []
        for row in rows:
            if start and row["slate_date"] < start:
                continue
            try:
                details = json.loads(row["details"])
            except (TypeError, ValueError):
                continue
            for edge in details.get("edges") or []:
                if not edge.get("signal") or edge["market"] == "moneyline":
                    continue  # ML 无线，不在本检验范围
                line = edge.get("line")
                if line is None:
                    continue
                entry = _entry_snapshot(
                    conn, row["game_id"], edge["market"], edge["side"], line, row["created_at"]
                )
                if not entry or entry.get("line") is None:
                    continue
                close = _closing_line(
                    conn, row["game_id"], edge["market"], edge["side"], line, row["tipoff"]
                )
                if not close or close.get("line") is None:
                    continue
                direction = _line_moves_toward(
                    edge["market"], edge["side"], float(entry["line"]), float(close["line"])
                )
                observations.append(
                    {
                        "game_id": row["game_id"],
                        "slate_date": row["slate_date"],
                        "signal_at": row["created_at"],
                        "market": edge["market"],
                        "side": edge["side"],
                        "signal_line": float(entry["line"]),
                        "closing_line": float(close["line"]),
                        "signal_price": entry["price"],
                        "closing_price": close["price"],
                        "direction": direction,
                    }
                )

        def _agg(items: list[dict]) -> dict:
            n = len(items)
            moved = [i for i in items if i["direction"] != 0]
            with_move = [i for i in items if i["direction"] in (1, -1)]
            toward = sum(1 for i in with_move if i["direction"] == 1)
            rate = toward / len(with_move) if with_move else None
            # 二项检验的 z 值（H0: p=0.5）
            z = None
            if with_move:
                p0, se = 0.5, math.sqrt(0.25 / len(with_move))
                z = (rate - p0) / se
            return {
                "n_signals": n,
                "n_with_move": len(with_move),
                "moved_toward": toward,
                "moved_against": len(with_move) - toward,
                "toward_rate": round(rate, 4) if rate is not None else None,
                "z_stat": round(z, 2) if z is not None else None,
            }

        by_market = {}
        for market in sorted({o["market"] for o in observations}):
            by_market[market] = _agg([o for o in observations if o["market"] == market])

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "start": start or "earliest",
            "horizon_note": "线移动取信号时刻 → 开赛前最后一笔同线快照",
            "overall": _agg(observations),
            "by_market": by_market,
            "rows": observations,
        }
        path = DATA_DIR / "linemove_report.json"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        return report
    finally:
        conn.close()


def print_report(report: dict) -> None:
    o = report["overall"]
    if not o["n_signals"]:
        print(
            "线移动检验：0 个观察。信号需要开盘口快照前后对照——"
            "实时盘口从 watch/Actions 开始积累后这里才有数据。"
        )
        return
    print(
        f"信号→线移动: {o['n_signals']} 条信号 · 其中 {o['n_with_move']} 条线有移动 · "
        f"朝信号方向 {o['moved_toward']} / 反向 {o['moved_against']}"
    )
    if o["toward_rate"] is not None:
        print(
            f"  顺线比例 {o['toward_rate']:.1%} (z={o['z_stat']}) — "
            + (
                "显著 >50%：信号包含市场尚未定价的信息（beat the close 的必要条件成立）"
                if o["z_stat"] and o["z_stat"] > 2
                else "未显著 >50%：信号尚不能预测线移动，继续积累或停在观察层"
            )
        )
    for market, s in report["by_market"].items():
        if s["n_with_move"]:
            print(
                f"  {market}: n={s['n_with_move']} 顺线 {s['toward_rate']:.1%} (z={s['z_stat']})"
            )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default=None, help="只统计该日期之后的信号")
    parser.add_argument("--hours", type=float, default=None, help="线移动观察窗口（小时）")
    args = parser.parse_args()
    rep = run(start=args.start, horizon_hours=args.hours)
    print_report(rep)
