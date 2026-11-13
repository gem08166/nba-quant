"""A 路：用历史「预测 × 结果」拟合按市场的 Platt 校准参数。

数据来源：`predictions`（每场取开赛前最后一版）+ 终局比分。只使用
model_prob 与胜负，不碰盘口价格——避免把市场公允价当校准目标（那会把
模型拉成市场的复制品）。

用法：`python -m app.cli calibrate`
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from ..pricing import calibrate as cal
from .signal_backtest import settle_edge


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def collect_pairs(conn: sqlite3.Connection) -> dict[str, list[tuple[float, int]]]:
    """按市场收集 (model_prob, 是否赢) 对。"""
    games = conn.execute(
        "SELECT id, tipoff, home_score, away_score FROM games "
        "WHERE status='STATUS_FINAL' AND home_score IS NOT NULL AND away_score IS NOT NULL"
    ).fetchall()
    pairs: dict[str, list[tuple[float, int]]] = {}
    for game in games:
        row = conn.execute(
            "SELECT details FROM predictions WHERE game_id=? "
            "AND (? IS NULL OR created_at <= ?) "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (game["id"], game["tipoff"], game["tipoff"]),
        ).fetchone()
        if row is None:
            continue
        try:
            details = json.loads(row["details"] or "{}")
        except (TypeError, ValueError):
            continue
        for edge in details.get("edges") or []:
            p = edge.get("model_prob")
            if p is None or not (0.0 < float(p) < 1.0):
                continue
            result = settle_edge(
                edge["market"], edge["side"], edge.get("line"),
                int(game["home_score"]), int(game["away_score"]),
            )
            if result == "push":
                continue
            pairs.setdefault(edge["market"], []).append(
                (float(p), 1 if result == "won" else 0)
            )
    return pairs


def fit_and_save(conn: sqlite3.Connection) -> dict:
    """拟合每个市场的校准参数并落库；样本不足的市场保留旧参数/跳过。"""
    pairs = collect_pairs(conn)
    stats: dict[str, dict] = {}
    for market, plist in sorted(pairs.items()):
        fit = cal.fit_platt(plist)
        if fit is None:
            stats[market] = {"skipped": True, "n": len(plist)}
            continue
        conn.execute(
            """
            INSERT INTO calibrators(created_at, market, a, b, n)
            VALUES(?, ?, ?, ?, ?)
            ON CONFLICT(market) DO UPDATE SET
                created_at=excluded.created_at, a=excluded.a, b=excluded.b, n=excluded.n
            """,
            (_now(), market, fit["a"], fit["b"], fit["n"]),
        )
        stats[market] = {**fit, "skipped": False}
    conn.commit()
    return stats


def load(conn: sqlite3.Connection) -> dict[str, dict]:
    """读取当前生效的校准参数：{market: {a, b, n}}。"""
    try:
        rows = conn.execute("SELECT market, a, b, n FROM calibrators").fetchall()
    except sqlite3.OperationalError:  # 旧库没有该表
        return {}
    return {row["market"]: {"a": row["a"], "b": row["b"], "n": row["n"]} for row in rows}
