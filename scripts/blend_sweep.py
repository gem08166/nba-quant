"""MARKET_BLEND 扫描（固化版）：在冻结 holdout 上回答"模型该占多少权重"。

对 blend ∈ {0, 0.15, 0.3, 0.45, 0.55, 0.7, 0.85, 1.0} 各做一次：
- 前 X% 场次拟合模型（与 holdout_eval 同一切分逻辑，同日比赛不拆）；
- 测试期每场用 SBR/FanDuel 收盘线模拟生产选边（模型融合概率 vs 收盘公允
  概率，取模型看好的一侧），按收盘价下注并结算；
- 输出 n / 命中率 / 收盘 ROI / 平均 edge / 最大 edge 分位 ROI。

判读：
- blend=0 是"纯市场线选边"的基线——它若最好，说明模型对选边无方向性贡献；
- 平均 edge 随 blend 上升而上升、ROI 随之下降 = 模型的乐观偏差，不是 edge；
- 最大 edge 分位显著差于整体 = "越自信越差"，正是 498 注历史检验的形态。

用途：每次改动模型/定价后重跑，确认没有让选边质量变差。
用法：python -m scripts.blend_sweep [--split 0.7] [--book sbr-fanduel]
输出：data/blend_sweep_report.json + 终端摘要
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import numpy as np

from app import db
from app.config import DATA_DIR
from app.jobs.signal_backtest import settle_edge
from app.models import ratings
from app.pricing.devig import cover_prob, fair_two_way, over_prob

BLENDS = (0.0, 0.15, 0.3, 0.45, 0.55, 0.7, 0.85, 1.0)


def _load_closing(conn, book: str) -> dict[str, dict[str, dict]]:
    rows = conn.execute(
        "SELECT game_id, market, side, line, price FROM odds_snapshots WHERE book=? ORDER BY id",
        (book,),
    ).fetchall()
    latest: dict[tuple[str, str, str], dict] = {}
    for r in rows:
        latest[(r["game_id"], r["market"], r["side"])] = dict(r)
    closing: dict[str, dict[str, dict]] = {}
    for (gid, market, side), row in latest.items():
        closing.setdefault(gid, {}).setdefault(market, {})[side] = row
    return closing


def run(split: float = 0.7, book: str = "sbr-fanduel", min_games: int = 100, min_closing: int = 30) -> dict:
    conn = db.connect()
    try:
        finals = sorted(ratings.load_finals(conn), key=lambda g: (g["slate_date"], g["id"]))
        if len(finals) < min_games:
            return {"error": f"not enough finals: {len(finals)}"}

        cut = int(len(finals) * split)
        cutoff = finals[cut]["slate_date"]
        while cut < len(finals) and finals[cut]["slate_date"] == cutoff:
            cut += 1
        train, test = finals[:cut], finals[cut:]
        cutoff = test[0]["slate_date"]

        model = ratings.fit(conn, as_of=cutoff)
        _, elo_final = ratings.compute_elo(train)
        rest_full = ratings.compute_rest(train)

        preds = {}
        for g in test:
            p = ratings.predict(
                model, int(g["home_id"]), int(g["away_id"]),
                elo_final.get(int(g["home_id"]), 1500.0),
                elo_final.get(int(g["away_id"]), 1500.0),
                rest_full.get(int(g["home_id"]), 7),
                rest_full.get(int(g["away_id"]), 7),
            )
            preds[g["id"]] = (g, p)

        closing = _load_closing(conn, book)
        covered = [gid for gid in preds if gid in closing]
        if len(covered) < min_closing:
            return {"error": f"only {len(covered)} test games have {book} closing odds"}

        results = {}
        for blend in BLENDS:
            rows: list[dict] = []
            for gid in covered:
                g, p = preds[gid]
                cl = closing[gid]

                sp = cl.get("spread", {})
                if sp.get("home") and sp.get("away") and sp["home"].get("line") is not None:
                    line = sp["home"]["line"]
                    pair = fair_two_way(sp["home"]["price"], sp["away"]["price"])
                    if pair:
                        mu = blend * p["pred_margin"] + (1 - blend) * (-line)
                        p_home = cover_prob(mu, line, p["sigma_margin"])
                        take_home = p_home >= pair[0]
                        side = "home" if take_home else "away"
                        fair = pair[0] if take_home else pair[1]
                        price = sp["home"]["price"] if take_home else sp["away"]["price"]
                        prob = p_home if take_home else 1 - p_home
                        result = settle_edge(
                            "spread", side, line if side == "home" else -line,
                            g["home_score"], g["away_score"],
                        )
                        rows.append({
                            "market": "spread", "edge": prob - fair, "result": result,
                            "profit": (price - 1) if result == "won"
                            else (-1 if result == "lost" else 0),
                        })

                t = cl.get("total", {})
                if t.get("over") and t.get("under") and t["over"].get("line") is not None:
                    line = t["over"]["line"]
                    pair = fair_two_way(t["over"]["price"], t["under"]["price"])
                    if pair:
                        mu = blend * p["pred_total"] + (1 - blend) * line
                        p_over = over_prob(mu, line, p["sigma_total"])
                        take_over = p_over >= pair[0]
                        side = "over" if take_over else "under"
                        fair = pair[0] if take_over else pair[1]
                        price = t["over"]["price"] if take_over else t["under"]["price"]
                        prob = p_over if take_over else 1 - p_over
                        result = settle_edge("total", side, line, g["home_score"], g["away_score"])
                        rows.append({
                            "market": "total", "edge": prob - fair, "result": result,
                            "profit": (price - 1) if result == "won"
                            else (-1 if result == "lost" else 0),
                        })

            decided = [r for r in rows if r["result"] != "push"]
            hits = sum(1 for r in decided if r["result"] == "won")
            profit = sum(r["profit"] for r in rows)
            mean_edge = float(np.mean([r["edge"] for r in rows])) if rows else None
            edges = [r["edge"] for r in rows]
            top_q = float(np.quantile(edges, 0.75)) if edges else None
            top = [r for r in rows if r["edge"] >= top_q] if edges else []
            top_dec = [r for r in top if r["result"] != "push"]
            results[str(blend)] = {
                "n": len(rows),
                "decided": len(decided),
                "hit_rate": round(hits / len(decided), 4) if decided else None,
                "roi_at_closing": round(profit / len(decided), 4) if decided else None,
                "mean_edge": round(mean_edge, 4) if mean_edge is not None else None,
                "top_quartile_roi": (
                    round(sum(r["profit"] for r in top) / len(top_dec), 4) if top_dec else None
                ),
                "top_quartile_n": len(top),
            }

        best = max(
            (k for k in results if results[k]["roi_at_closing"] is not None),
            key=lambda k: results[k]["roi_at_closing"],
            default=None,
        )
        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "split": split,
            "book": book,
            "train_games": len(train),
            "test_games": len(test),
            "test_period": [test[0]["slate_date"], test[-1]["slate_date"]],
            "games_with_closing": len(covered),
            "blends": results,
            "best_blend": best,
        }
        path = DATA_DIR / "blend_sweep_report.json"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        return report
    finally:
        conn.close()


def print_report(report: dict) -> None:
    if report.get("error"):
        print(report["error"])
        return
    print(
        f"blend 扫描：{report['train_games']} 训 → {report['test_games']} 测"
        f"（{report['test_period'][0]} ~ {report['test_period'][1]}），"
        f"其中 {report['games_with_closing']} 场有 {report['book']} 收盘线"
    )
    print(f"{'blend':>6} {'n':>4} {'命中':>7} {'收盘ROI':>9} {'平均edge':>9} {'TopQ ROI':>9}")
    for blend, s in report["blends"].items():
        if not s["n"]:
            continue
        troi = (
            f"{s['top_quartile_roi']:+.1%}" if s["top_quartile_roi"] is not None else "—"
        )
        hit = f"{s['hit_rate']:.1%}" if s["hit_rate"] is not None else "—"
        roi = f"{s['roi_at_closing']:+.2%}" if s["roi_at_closing"] is not None else "—"
        edge = f"{s['mean_edge']:+.2%}" if s["mean_edge"] is not None else "—"
        print(f"{blend:>6} {s['n']:>4} {hit:>7} {roi:>9} {edge:>9} {troi:>9}")
    if report.get("best_blend"):
        print(f"最优 blend = {report['best_blend']}（仅作参考：单点最优可能是噪声，看趋势）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", type=float, default=0.7)
    parser.add_argument("--book", default="sbr-fanduel")
    args = parser.parse_args()
    rep = run(split=args.split, book=args.book)
    print_report(rep)
