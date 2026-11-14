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
    """rapm.suggest_impacts 的薄包装（测试 monkeypatch 点）。

    model 语义：**RAPM 模型**（rapm.latest_model 的产物）；None=自动取最新。
    注意与 predict 的 team-ratings 模型是两套东西，勿混传。
    """
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

    model = RAPM 模型（None=自动取最新 rapm_model），不是 team-ratings 模型。
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


def side_of(edge: dict) -> str | None:
    """edge.side（home/away；兼容 h/a）→ 球队侧；大小分（ov/ud）无球队侧返回 None。"""
    raw = (edge.get("side") or "").casefold()
    return {"home": "home", "away": "away", "h": "home", "a": "away"}.get(raw)


def _team_short(conn: sqlite3.Connection, team_id: int) -> str:
    row = conn.execute("SELECT abbrev FROM teams WHERE id=?", (team_id,)).fetchone()
    return (row["abbrev"] if row else "") or str(team_id)


def apply_injury_gate(
    conn: sqlite3.Connection,
    game_id: str,
    edges: list[dict],
    model: dict | None = None,
) -> list[dict]:
    """伤停闸门（bettable 的最后一道）：下注侧 out/doubtful 缺人合计 RAPM 分值
    ≥ INJURY_GATE_POINTS → 降级为观察并注明缺谁缺多少。in-place 改写并返回同一列表。

    model = RAPM 模型（None=自动取最新），非 team-ratings 模型。
    大小分（ov/ud）无球队侧，不触发；阈值默认 3.0 分（约半个首发级别的影响）。
    """
    if not INJURY_GATE_ENABLE or not edges:
        return edges
    rows = game_injuries(conn, game_id)
    if not rows:
        return edges
    suggestions = {
        (s["team_id"], s["player"]): s for s in _suggestions(conn, game_id, model=model)
    }
    by_side: dict[str, list[str]] = {"home": [], "away": []}
    points: dict[str, float] = {"home": 0.0, "away": 0.0}
    for row in rows:
        sug = suggestions.get((row["team_id"], row["player"]))
        impact = float(sug["suggested_impact"]) if sug else float(row["impact"] or 0.0)
        if impact <= 0:
            continue
        side = "home" if row["team_id"] == _home_team_id(conn, game_id) else "away"
        # 只累计会真实降低该侧战力的值（out/doubtful/questionable 才有建议）
        if sug is None and (row.get("status") or "").casefold() not in ("out", "doubtful", "questionable", "day-to-day"):
            continue
        by_side[side].append(f"{row['player']} {impact:.1f}")
        points[side] += impact
    if not by_side["home"] and not by_side["away"]:
        return edges
    for edge in edges:
        if not edge.get("bettable"):
            continue
        side = side_of(edge)
        if side is None or points.get(side, 0.0) < INJURY_GATE_POINTS:
            continue
        team = _team_short(conn, _home_team_id(conn, game_id) if side == "home" else _away_team_id(conn, game_id))
        names = ", ".join(sorted(by_side[side], reverse=True))
        note = f"伤停闸门：{side} 队缺人合计 {points[side]:.1f} 分（{names}）"
        edge["bettable"] = False
        edge["bettable_note"] = note
        edge["gated"] = note
    return edges


def _home_team_id(conn: sqlite3.Connection, game_id: str) -> int | None:
    row = conn.execute("SELECT home_id FROM games WHERE id=?", (str(game_id),)).fetchone()
    return int(row["home_id"]) if row else None


def _away_team_id(conn: sqlite3.Connection, game_id: str) -> int | None:
    row = conn.execute("SELECT away_id FROM games WHERE id=?", (str(game_id),)).fetchone()
    return int(row["away_id"]) if row else None
