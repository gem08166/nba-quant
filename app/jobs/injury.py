"""伤停管线（0.87）：去重读取 → RAPM 建议自动采纳 → 下注闸门 → 决策快照。

背景：`injuries` 表唯一键含 status，同一球员状态变化会插新行、旧行残留
（如 day-to-day + out 并存），所有消费方必须经 `game_injuries` 去重取最新行。

impact 单位 = 净胜分点，`ratings.predict` 的语义：
margin -= injury_home; margin += injury_away; total -= 0.5*(injury_home+injury_away)。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from ..config import INJURY_GATE_ENABLE, INJURY_GATE_POINTS
from ..models import rapm

# 写 applied_at / 内部时间戳的统一格式（与库内 captured_at 一致）
_NOW_FMT = "%Y-%m-%dT%H:%M:%S+00:00"


def _now() -> str:
    return datetime.now(timezone.utc).strftime(_NOW_FMT)


def latest_by_player(rows: list) -> dict:
    """按 (game_id, team_id, player) 分组取最新 captured_at 的行（并列取 rowid 大者）。"""
    best: dict[tuple, tuple] = {}
    for row in rows:
        key = (row["game_id"], row["team_id"], row["player"])
        rank = (row["captured_at"] or "", row["id"])
        if key not in best or rank > best[key][0]:
            best[key] = (rank, row)
    return {key: value[1] for key, value in best.items()}


def game_injuries(conn: sqlite3.Connection, game_id: str) -> list[dict]:
    """某场比赛的伤停（按球员去重取最新行），按 (team_id, player) 排序。"""
    rows = conn.execute(
        "SELECT * FROM injuries WHERE game_id=?", (str(game_id),)
    ).fetchall()
    deduped = latest_by_player(rows)
    return [dict(deduped[k]) for k in sorted(deduped, key=lambda k: (k[1], k[2]))]


def _suggestions(conn: sqlite3.Connection, game_id: str, model: dict | None = None) -> list[dict]:
    """rapm.suggest_impacts 的薄包装（测试 monkeypatch 点）。"""
    return rapm.suggest_impacts(conn, game_id, model=model)


def snapshot(conn: sqlite3.Connection, game_id: str) -> list[dict]:
    """决策时刻的伤停快照（signal_log.injuries 的 JSON 形状）。"""
    return [
        {
            "team_id": r["team_id"],
            "player": r["player"],
            "status": r["status"],
            "impact": float(r["impact"] or 0.0),
            "impact_source": r["impact_source"] or "auto",
        }
        for r in game_injuries(conn, game_id)
    ]


def apply_impacts(
    conn: sqlite3.Connection,
    game_ids: list[str],
    model: dict | None = None,
    force: bool = False,
) -> dict:
    """把 RAPM 建议自动写入 injuries.impact（predict_date 每轮预测前调用）。

    规则：
    - impact_source='manual' 的行永不覆盖（用户手设值最权威）；
    - 其余行按去重后的最新行采纳：有建议 → impact=suggested_impact；
      无建议（状态不在因子表 / 匹配不到 PBP id）→ impact=0（复活归零）；
    - force=True 时连 manual 行也重算（CLI 显式重建用，日常流程不用）。

    返回 {"applied": 采纳数, "updated": 有变化数, "skipped": manual 跳过数, "zeroed": 归零数}。
    """
    now = _now()
    stats = {"applied": 0, "updated": 0, "skipped": 0, "zeroed": 0}
    for game_id in game_ids:
        suggestions = {
            (s["team_id"], s["player"]): s for s in _suggestions(conn, game_id, model=model)
        }
        for row in game_injuries(conn, game_id):
            key = (row["team_id"], row["player"])
            source = row.get("impact_source") or "auto"
            if source == "manual" and not force:
                stats["skipped"] += 1
                continue
            sug = suggestions.get(key)
            new_impact = float(sug["suggested_impact"]) if sug else 0.0
            new_status = (sug["status"] if sug else row["status"]) or None
            old_impact = float(row["impact"] or 0.0)
            changed = abs(new_impact - old_impact) > 1e-9
            conn.execute(
                """
                UPDATE injuries
                SET impact=?, impact_source='auto', applied_status=?, applied_at=?
                WHERE id=?
                """,
                (new_impact, new_status, now, row["id"]),
            )
            stats["applied"] += 1
            if changed:
                stats["updated"] += 1
            if not sug:
                stats["zeroed"] += 1
    return stats
