from __future__ import annotations

"""信号级回测：把历史预测里的 edge 当作纸面注单，用当时可拿到的价格入场、
收盘前最后一笔同线快照作为收盘价，按最终比分结算，输出 ROI/命中率/CLV。

与 backtest（MAE/Brier，回答"预测准不准"）互补——本模块回答"照着信号下注
赚不赚钱"，是整个系统价值的最终指标。

数据依赖：odds_snapshots 的历史积累。盘口快照太少时样本会很小甚至为空——
这是预期行为，watch 跑得越久越有意义。
"""

import json
import math
import sqlite3
from datetime import datetime, timezone

from .. import db
from ..config import DATA_DIR


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _entry_snapshot(
    conn: sqlite3.Connection,
    game_id: str,
    market: str,
    side: str,
    line: float | None,
    created_at: str | None,
) -> dict | None:
    """下注时点可拿到的价格：与预测同时刻（<=）最近的一笔同市场同线快照。"""
    created = _parse_ts(created_at)
    rows = conn.execute(
        """
        SELECT line, price, captured_at FROM odds_snapshots
        WHERE game_id=? AND market=? AND side=? AND price IS NOT NULL
        ORDER BY id
        """,
        (game_id, market, side),
    ).fetchall()
    candidates = [
        row
        for row in rows
        if (line is None or row["line"] is None or abs(row["line"] - line) < 1e-9)
        and (created is None or (_parse_ts(row["captured_at"]) or created) <= created)
    ]
    return dict(candidates[-1]) if candidates else None


def _closing_price(
    conn: sqlite3.Connection,
    game_id: str,
    market: str,
    side: str,
    line: float | None,
    tipoff: str | None,
) -> float | None:
    """收盘价：开赛前最后一笔同市场同线快照。"""
    tip = _parse_ts(tipoff)
    rows = conn.execute(
        """
        SELECT line, price, captured_at FROM odds_snapshots
        WHERE game_id=? AND market=? AND side=? AND price IS NOT NULL
        ORDER BY id
        """,
        (game_id, market, side),
    ).fetchall()
    candidates = [
        row
        for row in rows
        if (line is None or row["line"] is None or abs(row["line"] - line) < 1e-9)
        and (tip is None or (_parse_ts(row["captured_at"]) or tip) <= tip)
    ]
    return candidates[-1]["price"] if candidates else None


def settle_edge(market: str, side: str, line: float | None, home_score: int, away_score: int) -> str:
    """按最终比分结算单边注单：won / lost / push。"""
    margin = home_score - away_score
    total = home_score + away_score
    if market == "moneyline":
        if margin == 0:
            return "push"
        return "won" if (margin > 0) == (side == "home") else "lost"
    if market == "spread":
        # home line: margin + line > 0 覆盖；away line（= -home_line）：-margin + line > 0
        covered = (margin + line) if side == "home" else (-margin + line)
        if covered == 0:
            return "push"
        return "won" if covered > 0 else "lost"
    if market == "total":
        if total == line:
            return "push"
        return "won" if (total > line) == (side == "over") else "lost"
    return "push"


def closing_comparison(conn: sqlite3.Connection) -> dict:
    """模型 vs 收盘共识：对每场已完赛且有盘口快照的比赛，比较模型融合概率与
    收盘多书共识公允概率（edge = 模型概率 − 共识公允），并模拟在收盘价下注的 ROI。
    这是"模型是否跑赢市场"的标准度量，不依赖信号触发，样本=所有有快照的完赛场。"""
    from .predict import consensus_market

    games = conn.execute(
        """
        SELECT id, slate_date, tipoff, home_score, away_score FROM games
        WHERE home_score IS NOT NULL AND away_score IS NOT NULL
        """
    ).fetchall()
    rows: list[dict] = []
    for game in games:
        tip = _parse_ts(game["tipoff"])
        pred = conn.execute(
            "SELECT * FROM predictions WHERE game_id=? ORDER BY id DESC LIMIT 1", (game["id"],)
        ).fetchone()
        if not pred or not tip:
            continue
        try:
            details = json.loads(pred["details"])
        except (TypeError, ValueError):
            continue
        probs = details.get("probabilities") or {}
        snapshot_rows = conn.execute(
            "SELECT * FROM odds_snapshots WHERE game_id=?", (game["id"],)
        ).fetchall()
        # 只保留开赛前（收盘）的快照，再按 (book, market, side) 取最新
        latest: dict[tuple[str, str, str], dict] = {}
        for row in snapshot_rows:
            captured = _parse_ts(row["captured_at"])
            if captured and captured <= tip:
                latest[(row["book"], row["market"], row["side"])] = dict(row)
        if not latest:
            continue
        cons = consensus_market(list(latest.values()))

        def _bet(market: str, model_prob: float | None, side_a: str, side_b: str) -> dict | None:
            cons_m = cons.get(market)
            if not cons_m or model_prob is None:
                return None
            fair_a = cons_m.get("fair_" + side_a)
            fair_b = cons_m.get("fair_" + side_b)
            if fair_a is None or fair_b is None:
                return None
            # 模型选边：模型概率高的一侧
            if market == "spread" or market == "total":
                take_a = model_prob >= 0.5
            else:
                take_a = model_prob >= 0.5
            side = side_a if take_a else side_b
            fair = fair_a if take_a else fair_b
            price = cons_m.get(f"{side_a}_price" if take_a else f"{side_b}_price")
            if not price:
                return None
            line = cons_m.get("line")
            result = settle_edge(
                market,
                side if market != "total" else side,
                line,
                game["home_score"],
                game["away_score"],
            )
            profit = (price - 1.0) if result == "won" else (-1.0 if result == "lost" else 0.0)
            return {
                "game_id": game["id"],
                "slate_date": game["slate_date"],
                "market": market,
                "side": side,
                "model_prob": model_prob,
                "closing_fair": fair,
                "edge": model_prob - fair,
                "price": price,
                "result": result,
                "profit": profit,
            }

        for market, model_prob, side_a, side_b in (
            ("spread", probs.get("spread_home"), "home", "away"),
            ("total", probs.get("total_over"), "over", "under"),
            ("moneyline", probs.get("moneyline_home"), "home", "away"),
        ):
            bet = _bet(market, model_prob, side_a, side_b)
            if bet:
                rows.append(bet)

    def _agg(items: list[dict]) -> dict:
        decided = [i for i in items if i["result"] != "push"]
        wins = sum(1 for i in decided if i["result"] == "won")
        profit = sum(i["profit"] for i in items)
        mean_edge = sum(i["edge"] for i in items) / len(items) if items else None
        return {
            "n": len(items),
            "wins": wins,
            "roi": round(profit / len(decided), 4) if decided else None,
            "mean_edge_vs_closing": round(mean_edge, 4) if mean_edge is not None else None,
        }

    by_market = {}
    for market in sorted({r["market"] for r in rows}):
        by_market[market] = _agg([r for r in rows if r["market"] == market])
    return {"n_games": len({r["game_id"] for r in rows}), "overall": _agg(rows), "by_market": by_market}


def run(start: str | None = None, end: str | None = None) -> dict:
    conn = db.connect()
    try:
        rows = conn.execute(
            """
            SELECT p.*, g.slate_date, g.home_score, g.away_score, g.tipoff
            FROM predictions p JOIN games g ON g.id = p.game_id
            WHERE g.home_score IS NOT NULL AND g.away_score IS NOT NULL
            ORDER BY p.id
            """
        ).fetchall()

        settled_rows: list[dict] = []
        skipped_no_entry = 0
        for row in rows:
            if start and row["slate_date"] < start:
                continue
            if end and row["slate_date"] > end:
                continue
            try:
                details = json.loads(row["details"])
            except (TypeError, ValueError):
                continue
            for edge in details.get("edges") or []:
                if not edge.get("signal"):
                    continue  # 只回测真正触发的信号
                entry = _entry_snapshot(
                    conn, row["game_id"], edge["market"], edge["side"], edge.get("line"), row["created_at"]
                )
                if not entry or not entry["price"]:
                    skipped_no_entry += 1
                    continue
                entry_price = float(entry["price"])
                closing = _closing_price(
                    conn, row["game_id"], edge["market"], edge["side"], edge.get("line"), row["tipoff"]
                )
                result = settle_edge(
                    edge["market"], edge["side"], entry["line"], row["home_score"], row["away_score"]
                )
                stake = 1.0
                if result == "won":
                    profit = stake * (entry_price - 1.0)
                elif result == "lost":
                    profit = -stake
                else:
                    profit = 0.0
                clv = None
                if closing:
                    clv = entry_price / closing - 1.0
                settled_rows.append(
                    {
                        "game_id": row["game_id"],
                        "slate_date": row["slate_date"],
                        "market": edge["market"],
                        "side": edge["side"],
                        "line": entry["line"],
                        "entry_price": entry_price,
                        "closing_price": closing,
                        "model_prob": edge.get("model_prob"),
                        "result": result,
                        "profit": profit,
                        "clv": clv,
                    }
                )

        def _aggregate(items: list[dict]) -> dict:
            decided = [i for i in items if i["result"] != "push"]
            wins = sum(1 for i in decided if i["result"] == "won")
            profit = sum(i["profit"] for i in items)
            clvs = [i["clv"] for i in items if i["clv"] is not None]
            return {
                "n": len(items),
                "wins": wins,
                "losses": len(decided) - wins,
                "pushes": len(items) - len(decided),
                "profit": round(profit, 3),
                "roi": round(profit / len(decided), 4) if decided else None,
                "hit_rate": round(wins / len(decided), 4) if decided else None,
                "clv_n": len(clvs),
                "avg_clv": round(sum(clvs) / len(clvs), 4) if clvs else None,
                "clv_positive_rate": round(sum(1 for c in clvs if c > 0) / len(clvs), 4) if clvs else None,
            }

        by_market: dict[str, dict] = {}
        for market in sorted({item["market"] for item in settled_rows}):
            by_market[market] = _aggregate([i for i in settled_rows if i["market"] == market])

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "overall": _aggregate(settled_rows),
            "by_market": by_market,
            "skipped_no_entry_snapshot": skipped_no_entry,
            "closing_comparison": closing_comparison(conn),
            "rows": settled_rows,
        }
        path = DATA_DIR / "signal_backtest.json"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        return report
    finally:
        conn.close()


def print_report(report: dict) -> None:
    closing = report.get("closing_comparison") or {}
    if closing.get("overall", {}).get("n"):
        overall = closing["overall"]
        print(
            f"模型 vs 收盘共识: {closing['n_games']} 场 · 样本 {overall['n']} · "
            f"平均edge {overall['mean_edge_vs_closing']:+.2%} · 收盘价下注 ROI {overall['roi']:+.2%}"
        )
        for market, stats in closing["by_market"].items():
            if stats["n"]:
                print(
                    f"  {market}: n={stats['n']} edge {stats['mean_edge_vs_closing']:+.2%} "
                    f"ROI {stats['roi']:+.2%}"
                )
    else:
        print("模型 vs 收盘共识：暂无带快照的完赛场次（数据从现在开始积累）")
    overall = report["overall"]
    if not overall["n"]:
        print(
            f"信号回测：0 个可结算样本（缺入场快照 {report['skipped_no_entry_snapshot']} 条）。"
            "watch 积累盘口快照后本报告才有意义。"
        )
        return
    print(
        f"信号回测: n={overall['n']} (胜{overall['wins']}/负{overall['losses']}/走{overall['pushes']}) "
        f"ROI {overall['roi']:+.2%} 命中 {overall['hit_rate']:.1%}"
    )
    if overall["avg_clv"] is not None:
        print(
            f"平均 CLV {overall['avg_clv']:+.2%} (n={overall['clv_n']}) · "
            f"CLV为正比例 {overall['clv_positive_rate']:.1%}"
        )
    for market, stats in report["by_market"].items():
        if stats["n"]:
            roi = f"{stats['roi']:+.2%}" if stats["roi"] is not None else "—"
            print(f"  {market}: n={stats['n']} 盈亏 {stats['profit']:+.2f} ROI {roi}")
