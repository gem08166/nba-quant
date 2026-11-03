"""模型 vs 收盘线历史回测（模型 CLV 的黄金标准检验）。

方法：
- 按月 walk-forward：每月 1 号用当月之前的已完赛比赛拟合模型（无未来函数），
  预测该月每场比赛。
- 市场侧：SBR(FanDuel) 收盘线（backfill_closing 回填的 book='sbr-fanduel'），
  去水得收盘公允概率；与生产一致地按 MARKET_BLEND 与模型融合。
- 对每场每市场：edge = 模型融合概率 − 收盘公允概率（模型看好的一侧）；
  模拟按收盘价下注该侧并按最终比分结算。
- n>0 且 mean edge 持续为正 + 收盘价下注 ROI 不为深负 = 模型有真实信息的证据。

用法：python -m scripts.model_clv_history --start 2025-10-21
输出：data/model_clv_report.json + 终端摘要
"""
from __future__ import annotations

import argparse
import json
import math
from collections import defaultdict
from datetime import datetime, timezone

import numpy as np

from app import db
from app.config import DATA_DIR, MARKET_BLEND, SIGMA_MARGIN_FLOOR, SIGMA_TOTAL_FLOOR
from app.jobs.signal_backtest import settle_edge
from app.models import ratings


def _devig_pair(price_a: float, price_b: float) -> tuple[float, float] | None:
    if not price_a or not price_b or price_a <= 1.0 or price_b <= 1.0:
        return None
    inv_a, inv_b = 1.0 / price_a, 1.0 / price_b
    total = inv_a + inv_b
    return inv_a / total, inv_b / total


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def load_closing(conn) -> dict[str, dict[str, dict]]:
    """{game_id: {market: {line, prices, fair}}}——book='sbr-fanduel' 的收盘快照。"""
    rows = conn.execute(
        "SELECT * FROM odds_snapshots WHERE book='sbr-fanduel' ORDER BY id"
    ).fetchall()
    latest: dict[tuple[str, str, str], dict] = {}
    for r in rows:
        latest[(r["game_id"], r["market"], r["side"])] = dict(r)
    out: dict[str, dict[str, dict]] = {}
    for (game_id, market, side), row in latest.items():
        out.setdefault(game_id, {}).setdefault(market, {})[side] = row
    return out


def run(start: str, end: str | None = None) -> dict:
    conn = db.connect()
    try:
        games = ratings.load_finals(conn)
        closing = load_closing(conn)
        games = [
            g for g in games
            if g["slate_date"] >= start and (end is None or g["slate_date"] <= end)
            and g["id"] in closing
        ]
        print(f"有收盘线的已完赛比赛: {len(games)}")
        if not games:
            return {"error": "no games with closing odds"}

        # 按月 walk-forward
        months = sorted({g["slate_date"][:7] for g in games})
        rows: list[dict] = []
        for month in months:
            try:
                model = ratings.fit(conn, as_of=f"{month}-01")
            except ValueError:
                continue
            month_games = [g for g in games if g["slate_date"][:7] == month]
            pregame_elo, _ = ratings.compute_elo(
                [g for g in ratings.load_finals(conn) if g["slate_date"] < f"{month}-01"]
            )
            rest = ratings.compute_rest(
                [g for g in ratings.load_finals(conn) if g["slate_date"] < f"{month}-01"]
            )
            sigma_m = model["sigma_margin"]
            sigma_t = model["sigma_total"]
            for g in month_games:
                elo_h, elo_a = pregame_elo.get(int(g["home_id"]), 1500.0), pregame_elo.get(int(g["away_id"]), 1500.0)
                r_h, r_a = rest.get(int(g["home_id"]), 7), rest.get(int(g["away_id"]), 7)
                pred = ratings.predict(model, int(g["home_id"]), int(g["away_id"]), elo_h, elo_a, r_h, r_a)
                mu_m, mu_t = pred["pred_margin"], pred["pred_total"]
                close = closing[g["id"]]

                # --- 让分 ---
                sp = close.get("spread", {})
                if sp.get("home") and sp.get("away") and sp["home"].get("line") is not None:
                    line = sp["home"]["line"]
                    pair = _devig_pair(sp["home"]["price"], sp["away"]["price"])
                    if pair:
                        market_mu = -line
                        pricing_mu = MARKET_BLEND * mu_m + (1 - MARKET_BLEND) * market_mu
                        p_home_model = 1 - _norm_cdf((math.floor(-line) + 0.5 - pricing_mu) / sigma_m)
                        fair_home, fair_away = pair
                        take_home = p_home_model >= fair_home  # 模型看好的侧
                        prob = p_home_model if take_home else 1 - p_home_model
                        fair = fair_home if take_home else fair_away
                        price = sp["home"]["price"] if take_home else sp["away"]["price"]
                        side = "home" if take_home else "away"
                        line_side = line if take_home else -line
                        result = settle_edge("spread", side, line_side, g["home_score"], g["away_score"])
                        profit = (price - 1.0) if result == "won" else (-1.0 if result == "lost" else 0.0)
                        rows.append({
                            "date": g["slate_date"], "month": month, "market": "spread",
                            "side": side, "model_prob": prob, "closing_fair": fair,
                            "edge": prob - fair, "price": price, "result": result, "profit": profit,
                        })

                # --- 大小分 ---
                t = close.get("total", {})
                if t.get("over") and t.get("under") and t["over"].get("line") is not None:
                    line = t["over"]["line"]
                    pair = _devig_pair(t["over"]["price"], t["under"]["price"])
                    if pair:
                        pricing_mu_t = MARKET_BLEND * mu_t + (1 - MARKET_BLEND) * line
                        p_over_model = 1 - _norm_cdf((math.floor(line) + 0.5 - pricing_mu_t) / sigma_t)
                        fair_over, fair_under = pair
                        take_over = p_over_model >= fair_over
                        prob = p_over_model if take_over else 1 - p_over_model
                        fair = fair_over if take_over else fair_under
                        price = t["over"]["price"] if take_over else t["under"]["price"]
                        side = "over" if take_over else "under"
                        result = settle_edge("total", side, line, g["home_score"], g["away_score"])
                        profit = (price - 1.0) if result == "won" else (-1.0 if result == "lost" else 0.0)
                        rows.append({
                            "date": g["slate_date"], "month": month, "market": "total",
                            "side": side, "model_prob": prob, "closing_fair": fair,
                            "edge": prob - fair, "price": price, "result": result, "profit": profit,
                        })

                # --- 独赢 ---
                ml = close.get("moneyline", {})
                if ml.get("home") and ml.get("away"):
                    pair = _devig_pair(ml["home"]["price"], ml["away"]["price"])
                    if pair:
                        market_mu = 0.0  # ML 无线：市场预期即公允 50/50 吗？不——用让分线推市场 mu 更准，
                        # 但 ML 的公允直接来自去水价格；融合应作用于概率层面：
                        p_home_model_pure = _norm_cdf(mu_m / sigma_m)
                        fair_home, fair_away = pair
                        # 概率层面融合（与生产 build_market 的 mu 融合等价近似）
                        p_home_model = MARKET_BLEND * p_home_model_pure + (1 - MARKET_BLEND) * fair_home
                        take_home = p_home_model >= fair_home
                        prob = p_home_model if take_home else 1 - p_home_model
                        fair = fair_home if take_home else fair_away
                        price = ml["home"]["price"] if take_home else ml["away"]["price"]
                        side = "home" if take_home else "away"
                        result = settle_edge("moneyline", side, None, g["home_score"], g["away_score"])
                        profit = (price - 1.0) if result == "won" else (-1.0 if result == "lost" else 0.0)
                        rows.append({
                            "date": g["slate_date"], "month": month, "market": "moneyline",
                            "side": side, "model_prob": prob, "closing_fair": fair,
                            "edge": prob - fair, "price": price, "result": result, "profit": profit,
                        })

        def _agg(items: list[dict]) -> dict:
            decided = [i for i in items if i["result"] != "push"]
            wins = sum(1 for i in decided if i["result"] == "won")
            profit = sum(i["profit"] for i in items)
            mean_edge = sum(i["edge"] for i in items) / len(items) if items else None
            se = (np.std([i["edge"] for i in items]) / math.sqrt(len(items))) if len(items) > 2 else None
            return {
                "n": len(items),
                "mean_edge_vs_closing": round(mean_edge, 4) if mean_edge is not None else None,
                "edge_t_stat": round(mean_edge / se, 2) if mean_edge is not None and se else None,
                "roi_at_closing": round(profit / len(decided), 4) if decided else None,
                "hit_rate": round(wins / len(decided), 4) if decided else None,
            }

        overall = _agg(rows)
        by_market = {m: _agg([r for r in rows if r["market"] == m]) for m in sorted({r["market"] for r in rows})}
        by_month = {m: _agg([r for r in rows if r["month"] == m]) for m in months}

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "start": start, "end": end or "latest",
            "n_games": len(games),
            "overall": overall, "by_market": by_market, "by_month": by_month,
            "rows": rows,
        }
        path = DATA_DIR / "model_clv_report.json"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        return report
    finally:
        conn.close()


def print_report(report: dict) -> None:
    if report.get("error"):
        print(report["error"])
        return
    o = report["overall"]
    print(f"模型 vs 收盘线（SBR/FanDuel）: {report['n_games']} 场 · 样本 {o['n']}")
    print(f"  平均 edge {o['mean_edge_vs_closing']:+.2%} (t={o['edge_t_stat']}) · 收盘价下注 ROI {o['roi_at_closing']:+.2%} · 命中 {o['hit_rate']:.1%}")
    print("  分市场:")
    for market, s in report["by_market"].items():
        if s["n"]:
            print(f"    {market:>9}: n={s['n']:4d} edge {s['mean_edge_vs_closing']:+.2%} (t={s['edge_t_stat']}) ROI {s['roi_at_closing']:+.2%}")
    print("  分月:")
    for month, s in report["by_month"].items():
        if s["n"]:
            print(f"    {month}: n={s['n']:4d} edge {s['mean_edge_vs_closing']:+.2%} ROI {s['roi_at_closing']:+.2%}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", default="2025-10-21")
    parser.add_argument("--end", default=None)
    args = parser.parse_args()
    report = run(start=args.start, end=args.end)
    print_report(report)
