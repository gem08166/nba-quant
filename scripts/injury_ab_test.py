"""伤停自动采纳 A/B 验收（一次性，只读不写）。

对 11-03→11-13 的已完赛比赛（skip 今日未完赛）：
- 每场取**赛前最后一次**伤停快照（captured_at <= tipoff 的最后一批），
  按球员去重后纯内存计算 injury_home/away（manual 行取手设值）；
- 用当前最新模型重算两版预测：baseline（impact=0） vs auto（伤停修正）；
- 与最终比分比净胜分 MAE；
- 收盘线回归：对有收盘线的场次，regress (pred - close) on (actual - close)，
  β 显著 > 0 说明预测相对收盘线有真实信息量（既有验收标准）；
- 两版各自「按收盘价可下注」的 ROI 对比。

输出 stdout 摘要 + data/injury_ab_report.json。全程只读。
"""
from __future__ import annotations

import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import db  # noqa: E402
from app.models import ratings, rapm  # noqa: E402
from app.jobs import injury  # noqa: E402

START, END = "2026-11-03", "2026-11-13"
MIN_SAMPLE = 30
OUT = Path("data/injury_ab_report.json")


def _ts(s: str) -> datetime:
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def pre_tipoff_injuries(conn: sqlite3.Connection, game: dict) -> list[dict]:
    """该场赛前（captured_at <= tipoff）的伤停，按球员去重取最新行。"""
    rows = conn.execute(
        "SELECT * FROM injuries WHERE game_id=? AND captured_at <= ?",
        (game["id"], game["tipoff"]),
    ).fetchall()
    deduped = injury.latest_by_player(rows)
    return [dict(deduped[k]) for k in sorted(deduped, key=lambda k: (k[1], k[2]))]


def impacts_from_rows(conn: sqlite3.Connection, game: dict, rows: list[dict], model: dict) -> tuple[float, float]:
    """纯内存版采纳：返回 (injury_home, injury_away)。

    A/B 特例：历史行 impact 全 0（自动采纳是 11-15 才上线的），所以对
    状态在因子表内的行用 RAPM 评分现算（model=RAPM 模型）；impact>0 的行
    （manual 或已采纳）尊重行内值。
    """
    home_id, away_id = int(game["home_id"]), int(game["away_id"])
    status_factor = {"out": 0.8, "doubtful": 0.8, "questionable": 0.4, "day-to-day": 0.4}
    id_by_name = {
        name.casefold(): pid for pid, name in (model.get("names") or {}).items()
    }
    injury_home = injury_away = 0.0
    for row in rows:
        impact = float(row["impact"] or 0.0)
        status = (row["status"] or "").casefold()
        if impact <= 0 and status in status_factor:
            pid = id_by_name.get((row["player"] or "").casefold())
            if pid is not None:
                rating = rapm.player_rating(model, int(pid))
                if rating:
                    impact = max(0.0, rating) * status_factor[status]
        if impact <= 0:
            continue
        if int(row["team_id"]) == home_id:
            injury_home += impact
        elif int(row["team_id"]) == away_id:
            injury_away += impact
    return injury_home, injury_away


def closing_odds(conn: sqlite3.Connection, game_id: str) -> dict | None:
    """每场最后一批 odds 快照（收盘口径）。键用 "market|side" 字符串（可 JSON 化）。"""
    row = conn.execute(
        """
        SELECT captured_at FROM odds_snapshots WHERE game_id=?
        ORDER BY captured_at DESC LIMIT 1
        """,
        (game_id,),
    ).fetchone()
    if not row:
        return None
    rows = conn.execute(
        "SELECT market, side, line, price FROM odds_snapshots WHERE game_id=? AND captured_at=?",
        (game_id, row["captured_at"]),
    ).fetchall()
    out: dict[str, dict] = {}
    for r in rows:
        key = f"{r['market']}|{r['side']}"
        if key not in out or (r["price"] or 0) > out[key]["price"]:
            out[key] = dict(r)
    return out


def regress_beta(xs: list[float], ys: list[float]) -> tuple[float, float, int]:
    """OLS: ys = a + b*xs。返回 (beta, se, n)。"""
    n = len(xs)
    if n < 10:
        return 0.0, 1.0, n
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    if sxx <= 0:
        return 0.0, 1.0, n
    beta = sxy / sxx
    resid = [y - (my + beta * (x - mx)) for x, y in zip(xs, ys)]
    sse = sum(r * r for r in resid)
    se = math.sqrt(sse / (n - 2) / sxx) if n > 2 else 1.0
    return beta, se, n


def main() -> None:
    conn = db.connect()
    try:
        model = ratings.latest_model(conn)
        rapm_model = rapm.latest_model(conn)
        if model is None:
            print("no model; run predict first")
            return
        if rapm_model is None:
            print("no RAPM model; run PBP backfill first")
            return
        games = [
            dict(r)
            for r in conn.execute(
                """
                SELECT * FROM games
                WHERE slate_date BETWEEN ? AND ? AND status='STATUS_FINAL'
                  AND home_score IS NOT NULL AND away_score IS NOT NULL
                  AND tipoff IS NOT NULL
                ORDER BY slate_date
                """,
                (START, END),
            )
        ]
        results = []
        for g in games:
            rows = pre_tipoff_injuries(conn, g)
            ih, ia = impacts_from_rows(conn, g, rows, rapm_model)
            actual = int(g["home_score"]) - int(g["away_score"])
            total_actual = int(g["home_score"]) + int(g["away_score"])
            elo = ratings.elo_as_of(conn, g["slate_date"])
            rest = ratings.rest_as_of(conn, g["slate_date"], [int(g["home_id"]), int(g["away_id"])])
            base = ratings.predict(model, int(g["home_id"]), int(g["away_id"]),
                                   elo.get(int(g["home_id"]), 1500.0), elo.get(int(g["away_id"]), 1500.0),
                                   rest.get(int(g["home_id"]), 7), rest.get(int(g["away_id"]), 7),
                                   0.0, 0.0, 0, 0)
            auto = ratings.predict(model, int(g["home_id"]), int(g["away_id"]),
                                   elo.get(int(g["home_id"]), 1500.0), elo.get(int(g["away_id"]), 1500.0),
                                   rest.get(int(g["home_id"]), 7), rest.get(int(g["away_id"]), 7),
                                   ih, ia, 0, 0)
            co = closing_odds(conn, g["id"])
            results.append({
                "game_id": g["id"],
                "slate": g["slate_date"],
                "actual_margin": actual,
                "actual_total": total_actual,
                "base_margin": base["pred_margin"],
                "auto_margin": auto["pred_margin"],
                "base_total": base["pred_total"],
                "auto_total": auto["pred_total"],
                "injury_home": ih,
                "injury_away": ia,
                "n_injuries": len(rows),
                "closing": co,
            })
        n = len(results)
        if n < MIN_SAMPLE:
            print(f"样本不足（{n} < {MIN_SAMPLE}），结果仅供参考")
        mae_base = sum(abs(r["base_margin"] - r["actual_margin"]) for r in results) / n
        mae_auto = sum(abs(r["auto_margin"] - r["actual_margin"]) for r in results) / n
        mae_total_base = sum(abs(r["base_total"] - r["actual_total"]) for r in results) / n
        mae_total_auto = sum(abs(r["auto_total"] - r["actual_total"]) for r in results) / n

        # 收盘线回归：仅取有收盘 spread 的场次（x=pred-close, y=actual-close）
        def beta_pair(key):
            xs, ys = [], []
            for r in results:
                cl = (r["closing"] or {}).get("spread|home")
                if not cl or cl.get("line") is None:
                    continue
                close_margin = -float(cl["line"])
                xs.append(r[key] - close_margin)
                ys.append(r["actual_margin"] - close_margin)
            return regress_beta(xs, ys), len(xs)

        (b_base, se_base, _), nb = beta_pair("base_margin")
        (b_auto, se_auto, _), na = beta_pair("auto_margin")

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "window": [START, END],
            "n_games": n,
            "n_games_with_injuries": sum(1 for r in results if r["n_injuries"]),
            "mae_margin": {"baseline": round(mae_base, 3), "auto": round(mae_auto, 3)},
            "mae_total": {"baseline": round(mae_total_base, 3), "auto": round(mae_total_auto, 3)},
            "closing_beta": {
                "baseline": {"beta": round(b_base, 3), "se": round(se_base, 3), "n": nb},
                "auto": {"beta": round(b_auto, 3), "se": round(se_auto, 3), "n": na},
            },
            "verdict": {
                "mae_margin_improved": mae_auto < mae_base,
                "beta_not_worse": b_auto >= b_base - 0.05,
                "beta_significant_auto": b_auto > 2 * se_auto,
            },
            "results": results,
        }
        OUT.write_text(json.dumps(report, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"n={n} (有伤停 {report['n_games_with_injuries']})")
        print(f"MAE margin: baseline {mae_base:.3f} vs auto {mae_auto:.3f}")
        print(f"MAE total : baseline {mae_total_base:.3f} vs auto {mae_total_auto:.3f}")
        print(f"β(close)  : baseline {b_base:.3f}±{se_base:.3f} (n={nb}) vs auto {b_auto:.3f}±{se_auto:.3f} (n={na})")
        print("verdict:", json.dumps(report["verdict"], ensure_ascii=False))
        print(f"report -> {OUT}")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
