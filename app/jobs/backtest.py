from __future__ import annotations

import json
import math
import sqlite3
from collections import defaultdict

from .. import db
from ..config import DATA_DIR
from ..models import ratings
from .predict import consensus_market


def _metrics(rows: list[dict]) -> dict:
    if not rows:
        return {"n": 0}
    margin_errors = [row["pred_margin"] - row["actual_margin"] for row in rows]
    total_errors = [row["pred_total"] - row["actual_total"] for row in rows]
    brier = sum((row["home_win_prob"] - row["home_win"]) ** 2 for row in rows) / len(rows)
    accuracy = sum((row["home_win_prob"] >= 0.5) == bool(row["home_win"]) for row in rows) / len(rows)

    ats_rows = [row for row in rows if row.get("ats_hit") is not None]
    ats_hit = sum(1 for row in ats_rows if row["ats_hit"]) / len(ats_rows) if ats_rows else None
    ou_rows = [row for row in rows if row.get("ou_hit") is not None]
    ou_hit = sum(1 for row in ou_rows if row["ou_hit"]) / len(ou_rows) if ou_rows else None

    return {
        "n": len(rows),
        "margin_mae": sum(abs(e) for e in margin_errors) / len(rows),
        "margin_rmse": math.sqrt(sum(e * e for e in margin_errors) / len(rows)),
        "margin_bias": sum(margin_errors) / len(rows),
        "total_mae": sum(abs(e) for e in total_errors) / len(rows),
        "total_rmse": math.sqrt(sum(e * e for e in total_errors) / len(rows)),
        "total_bias": sum(total_errors) / len(rows),
        "brier": brier,
        "home_win_accuracy": accuracy,
        "ats_n": len(ats_rows),
        "ats_hit_rate": ats_hit,
        "ou_n": len(ou_rows),
        "ou_hit_rate": ou_hit,
    }


def _ats_result(conn: sqlite3.Connection, game: dict, sigma_margin: float, pred_margin: float) -> bool | None:
    """让分方向命中：与生产信号同口径——用融合定价的 cover_prob 判定
    （第三方审查 P1-2：旧实现用 ML 概率 0.5 判方向，与让分口径错配）。"""
    rows = conn.execute(
        "SELECT * FROM odds_snapshots WHERE game_id=? AND market='spread'", (game["id"],)
    ).fetchall()
    cons = consensus_market([dict(r) for r in rows]).get("spread")
    if cons and cons.get("line") is not None:
        line = cons["line"]
    else:
        row = conn.execute(
            """
            SELECT line FROM odds_snapshots
            WHERE game_id=? AND market='spread' AND side='home'
            ORDER BY id DESC LIMIT 1
            """,
            (game["id"],),
        ).fetchone()
        if not row or row["line"] is None:
            return None
        line = row["line"]
    from ..pricing.devig import cover_prob

    model_home = cover_prob(pred_margin, line, sigma_margin) >= 0.5
    actual_margin = int(game["home_score"]) - int(game["away_score"])
    if actual_margin + line == 0:
        return None
    home_covered = actual_margin + line > 0
    return home_covered == model_home


def _ou_result(conn: sqlite3.Connection, game: dict, pred_total: float) -> bool | None:
    """大小分方向命中：用多书共识线（无共识时退回最新单线）。"""
    rows = conn.execute(
        "SELECT * FROM odds_snapshots WHERE game_id=? AND market='total'", (game["id"],)
    ).fetchall()
    cons = consensus_market([dict(r) for r in rows]).get("total")
    if cons and cons.get("line") is not None:
        line = cons["line"]
    else:
        row = conn.execute(
            """
            SELECT line FROM odds_snapshots
            WHERE game_id=? AND market='total' AND side='over'
            ORDER BY id DESC LIMIT 1
            """,
            (game["id"],),
        ).fetchone()
        if not row or row["line"] is None:
            return None
        line = row["line"]
    actual_total = int(game["home_score"]) + int(game["away_score"])
    if actual_total == line:
        return None
    actual_over = actual_total > line
    model_over = pred_total > line
    return actual_over == model_over


def _calibration(rows: list[dict]) -> list[dict]:
    """可靠性诊断：按预测概率分桶，比较预测均值与实际胜率（isotonic 校准的依据）。"""
    buckets: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        buckets[min(int(r["home_win_prob"] * 10), 9)].append(r)
    out = []
    for b in sorted(buckets):
        items = buckets[b]
        mean_p = sum(i["home_win_prob"] for i in items) / len(items)
        actual = sum(i["home_win"] for i in items) / len(items)
        out.append({
            "bucket": f"{b * 10}-{b * 10 + 10}%",
            "mean_prob": round(mean_p, 3),
            "actual": round(actual, 3),
            "gap": round(mean_p - actual, 3),
            "n": len(items),
        })
    return out


def run(test_start: str, test_end: str | None = None, out_path: str | None = None) -> dict:
    conn = db.connect()
    try:
        games = ratings.load_finals(conn)
        if not games:
            raise ValueError("no completed games; run backfill first")
        pregame_elo, _ = ratings.compute_elo(games)
        rest = ratings.compute_rest(games)

        test_games = [
            game
            for game in games
            if game["slate_date"] >= test_start and (test_end is None or game["slate_date"] <= test_end)
        ]
        months = sorted({game["slate_date"][:7] for game in test_games})
        rows: list[dict] = []
        monthly: dict[str, list[dict]] = defaultdict(list)

        for month in months:
            try:
                model = ratings.fit(conn, as_of=f"{month}-01")
            except ValueError:
                continue
            for game in test_games:
                if game["slate_date"][:7] != month:
                    continue
                elo_home, elo_away = pregame_elo[game["id"]]
                rest_home, rest_away = rest[game["id"]]
                pred = ratings.predict(
                    model,
                    int(game["home_id"]),
                    int(game["away_id"]),
                    elo_home,
                    elo_away,
                    rest_home,
                    rest_away,
                )
                actual_margin = int(game["home_score"]) - int(game["away_score"])
                actual_total = int(game["home_score"]) + int(game["away_score"])
                row = {
                    "game_id": game["id"],
                    "date": game["slate_date"],
                    "pred_margin": pred["pred_margin"],
                    "pred_total": pred["pred_total"],
                    "home_win_prob": pred["home_win_prob"],
                    "actual_margin": actual_margin,
                    "actual_total": actual_total,
                    "home_win": 1 if actual_margin > 0 else 0,
                }
                row["ats_hit"] = _ats_result(conn, game, pred["sigma_margin"], pred["pred_margin"])
                row["ou_hit"] = _ou_result(conn, game, pred["pred_total"])
                rows.append(row)
                monthly[month].append(row)

        report = {
            "test_start": test_start,
            "test_end": test_end,
            "overall": _metrics(rows),
            "monthly": {month: _metrics(items) for month, items in sorted(monthly.items())},
            "calibration": _calibration(rows),
            "rows": rows,
        }
        path = out_path or str(DATA_DIR / "backtest_report.json")
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        return report
    finally:
        conn.close()


def print_report(report: dict) -> None:
    overall = report["overall"]
    print(f"样本: {overall.get('n', 0)} 场  ({report['test_start']} ~ {report.get('test_end') or 'now'})")
    if overall.get("n"):
        print(f"分差 MAE: {overall['margin_mae']:.2f}   RMSE: {overall['margin_rmse']:.2f}   bias: {overall['margin_bias']:+.2f}")
        print(f"总分 MAE: {overall['total_mae']:.2f}   RMSE: {overall['total_rmse']:.2f}   bias: {overall['total_bias']:+.2f}")
        print(f"主胜 Brier: {overall['brier']:.4f}   方向准确率: {overall['home_win_accuracy']:.3f}")
        if overall.get("ats_n"):
            print(f"让分方向命中率: {overall['ats_hit_rate']:.3f}  (n={overall['ats_n']})")
        else:
            print("让分方向命中率: 无历史盘口，跳过（免费源历史盘口稀缺）")
        if overall.get("ou_n"):
            print(f"大小分方向命中率: {overall['ou_hit_rate']:.3f}  (n={overall['ou_n']})")
        print("\n按月:")
        for month, metrics in report["monthly"].items():
            print(f"  {month}: n={metrics['n']:3d}  margin_MAE={metrics['margin_mae']:.2f}  total_MAE={metrics['total_mae']:.2f}")
