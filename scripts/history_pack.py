"""为静态导出生成"历史比赛日复盘包"。

背景：静态模式（GitHub Pages / 手机 PWA）原来只导出"当前比赛日"的看板数据，
跳到别的比赛日就没有赛前预测/盘口/信号历史 → 赛后复盘板块是空的。

这里导出最近 N 个比赛日的每场比赛：
- 赛前预测（分差/总分/σ/主胜概率）与盘口线
- 该场历史上出现过的信号（去重聚合，带命中/落空结果）
- 最终比分与分节得分
- 纸面注单（本机 localStorage 合并用）

结构（放在 payload.history 里）：
{
  "2026-11-04": [
     {game_id, matchup, matchup_zh, home_name, home_name_zh, away_name, away_name_zh,
      home_score, away_score, home_linescores, away_linescores,
      pregame: {pred_margin, pred_total, sigma_margin, sigma_total, home_win_prob},
      lines: {spread, total},
      signals_history: [...]},
     ...
  ]
}
"""
from __future__ import annotations

import json
import sqlite3
from collections import defaultdict

from app.jobs.live import merge_signal_versions, settle_edge
from app import i18n


def build_history(
    conn: sqlite3.Connection, slate: str, days: int = 10, max_games_per_day: int = 20
) -> dict:
    """导出最近 days 个比赛日的复盘数据。"""
    date_rows = conn.execute(
        """
        SELECT DISTINCT slate_date FROM games
        WHERE status = 'STATUS_FINAL' AND slate_date < ? AND slate_date >= date(?, '-30 days')
        ORDER BY slate_date DESC LIMIT ?
        """,
        (slate, slate, days),
    ).fetchall()
    dates = [r["slate_date"] for r in date_rows]

    history: dict[str, list[dict]] = {}
    for day in dates:
        games = conn.execute(
            """
            SELECT g.id, g.slate_date, g.tipoff, g.status, g.home_score, g.away_score,
                   h.abbrev AS home_abbrev, h.name AS home_name,
                   a.abbrev AS away_abbrev, a.name AS away_name
            FROM games g
            JOIN teams h ON h.id = g.home_id
            JOIN teams a ON a.id = g.away_id
            WHERE g.slate_date = ? AND g.status = 'STATUS_FINAL'
            ORDER BY g.tipoff
            LIMIT ?
            """,
            (day, max_games_per_day),
        ).fetchall()
        if not games:
            continue

        items = []
        for g in games:
            game_id = g["id"]
            # 赛前预测（最新一条）
            pred_row = conn.execute(
                """
                SELECT pred_margin, sigma_margin, pred_total, sigma_total,
                       home_win_prob, details
                FROM predictions WHERE game_id=? ORDER BY id DESC LIMIT 1
                """,
                (game_id,),
            ).fetchone()
            pregame = None
            lines = {}
            if pred_row:
                pregame = {
                    "pred_margin": pred_row["pred_margin"],
                    "sigma_margin": pred_row["sigma_margin"],
                    "pred_total": pred_row["pred_total"],
                    "sigma_total": pred_row["sigma_total"],
                    "home_win_prob": pred_row["home_win_prob"],
                }
                try:
                    details = json.loads(pred_row["details"]) or {}
                    lines = details.get("lines") or {}
                except (TypeError, ValueError):
                    lines = {}

            # 信号历史：该场全部 prediction 版本里 signal=True 的边缘（去重）
            sig_rows = conn.execute(
                """
                SELECT created_at, details FROM predictions
                WHERE game_id=? AND details LIKE ? ORDER BY id
                """,
                (game_id, '%"signal"%'),
            ).fetchall()
            signals = merge_signal_versions(
                [(r["created_at"], r["details"]) for r in sig_rows]
            )
            for s in signals:
                s["result"] = settle_edge(
                    s["market"], s["side"], s["line"], g["home_score"], g["away_score"]
                )

            # 分节得分（ESPN linescores 存在 pbp_plays 里不完整，这里用比分即可）
            matchup = f"{g['away_abbrev']} @ {g['home_abbrev']}"
            items.append(
                {
                    "game_id": game_id,
                    "matchup": matchup,
                    "matchup_zh": i18n.matchup_zh(matchup),
                    "home_name": g["home_name"],
                    "away_name": g["away_name"],
                    "home_name_zh": i18n.team_zh(g["home_abbrev"], g["home_name"]),
                    "away_name_zh": i18n.team_zh(g["away_abbrev"], g["away_name"]),
                    "tipoff": g["tipoff"],
                    "home_score": g["home_score"],
                    "away_score": g["away_score"],
                    "home_linescores": [],
                    "away_linescores": [],
                    "pregame": pregame,
                    "lines": lines,
                    "signals_history": signals,
                }
            )
        if items:
            history[day] = items

    return history
