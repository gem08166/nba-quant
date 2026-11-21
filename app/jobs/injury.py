"""伤停管线（0.87）：去重读取 → RAPM 建议自动采纳 → 下注闸门 → 决策快照。

背景：`injuries` 表唯一键含 status，同一球员状态变化会插新行、旧行残留
（如 day-to-day + out 并存），所有消费方必须经 `game_injuries` 去重取最新行。

impact 单位 = 净胜分点，`ratings.predict` 的语义：
margin -= injury_home; margin += injury_away; total -= 0.5*(injury_home+injury_away)。
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from ..config import (
    INJURY_GATE_ENABLE,
    INJURY_GATE_POINTS,
    INJURY_GATE_UNRESOLVED,
    INJURY_GATE_UNSCORED_PLAYED,
)
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


def _align_suggestion(suggestions: dict, row) -> dict | None:
    """从 (team_id, player) 索引的建议表里挑与去重行 status 一致的那条。

    rapm.suggest_impacts 对同球员多状态行会返回多条同键建议（不去重），
    dict 压缩时是 rowid 序碰运气（0.87 审查 F2）；这里显式按去重行的
    status 对齐，对不上再退回最后一条。
    """
    same_key = [
        s for s in suggestions.values()
        if s["team_id"] == row["team_id"] and s["player"] == row["player"]
    ]
    if not same_key:
        return None
    status = (row["status"] or "").casefold()
    for s in same_key:
        if (s.get("status") or "").casefold() == status:
            return s
    return same_key[-1]


def snapshot(conn: sqlite3.Connection, game_id: str, model: dict | None = None) -> list[dict]:
    """决策时刻的伤停快照（signal_log.injuries 的 JSON 形状）。

    impact 记录**决策口径**：manual 手设值，否则去重行对齐的建议值
    （AUTO_APPLY 关时库内 impact 恒 0，快照不带建议值就无法校准系数——
    0.87 审查 F5）。库内 impact 不被本函数改动。
    """
    rows = game_injuries(conn, game_id)
    suggestions = {
        (s["team_id"], s["player"]): s
        for s in _suggestions(conn, game_id, model=model)
    }
    out = []
    for r in rows:
        if (r.get("impact_source") or "auto") == "manual":
            impact = float(r["impact"] or 0.0)
        else:
            sug = _align_suggestion(suggestions, r)
            impact = float(sug["suggested_impact"]) if sug else 0.0
        out.append({
            "team_id": r["team_id"],
            "player": r["player"],
            "status": r["status"],
            "impact": impact,
            "impact_source": r["impact_source"] or "auto",
        })
    return out


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
            sug = _align_suggestion(suggestions, row)
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


def _season_appearance(conn: sqlite3.Connection, player: str) -> bool | None:
    """该球员本赛季是否有出场痕迹（players 表由 PBP 构建）。

    返回 True=有出场（若 RAPM 仍无建议 = 打分失败，危险的盲区）；
    None=解析不到或确认无出场记录——两者都无法核实真实影响：
    可能是名字变体（ESPN 名 ≠ PBP 名，打分失败），也可能是赛季未出场
    （impact=0 合理）。保守起见都按「未核实」进盲区桶（≥2 个才拦）。
    """
    name = (player or "").strip()
    if not name:
        return None
    try:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM players WHERE lower(name)=lower(?)", (name,)
        ).fetchone()
        if row and row["n"]:
            return True
        # 全名对不上时按姓氏（最后一个词）精确命中也算有出场
        surname = name.split()[-1].casefold()
        if not surname:
            return None
        rows = conn.execute("SELECT name FROM players").fetchall()
        for r in rows:
            if (r["name"] or "").casefold().split()[-1] == surname:
                return True
    except sqlite3.Error:
        return None  # 无 players 表（测试/空库）→ 无法核实
    return None  # players 表无行：无法核实（名字变体 or 赛季未出场）


# 闸门只认确证缺阵（规格 0.87：out/doubtful 才拦）。questionable/day-to-day 是
# 赛前投机状态，A/B 显示其碎片修正是主要噪音源（44/53 场 <3 分）。
_GATE_STATUSES = {"out", "doubtful"}


def apply_injury_gate(
    conn: sqlite3.Connection,
    game_id: str,
    edges: list[dict],
    model: dict | None = None,
) -> list[dict]:
    """伤停闸门（bettable 的最后一道），两道规则：

    1. 分值闸门（0.87）：下注侧 out/doubtful 缺人合计分值 ≥ INJURY_GATE_POINTS
       → 降级为观察并注明缺谁缺多少。
    2. 盲区闸门（0.99）：impact=0 且非 manual 的未评分缺阵——打分失败
       （有出场痕迹但 RAPM 无建议）≥1 个，或名字无法解析 ≥2 个 → 降级。
       大小分两侧任一触发即拦。

    in-place 改写并返回同一列表。

    model = RAPM 模型（None=自动取最新），非 team-ratings 模型。
    取值优先级：manual 手设 impact > 去重行对应的建议（0.87 审查 F2/F3）；
    大小分（ov/ud）无球队侧不触发分值闸门；阈值默认 3.0 分（约半个首发级别的影响）。
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
        status = (row["status"] or "").casefold()
        if status not in _GATE_STATUSES:
            continue
        # manual 手设值最权威；否则用与该行 status 对齐的建议（F2：同键多建议
        # 时取 status 匹配去重行的那条，取不到才退回 rowid 最后一条）
        if (row.get("impact_source") or "auto") == "manual":
            impact = float(row["impact"] or 0.0)
        else:
            sug = suggestions.get((row["team_id"], row["player"]))
            if sug is None:
                impact = float(row["impact"] or 0.0)
            elif sug.get("status", "").casefold() == status:
                impact = float(sug["suggested_impact"])
            else:
                impact = float(row["impact"] or 0.0)
        if impact <= 0:
            continue
        side = "home" if row["team_id"] == _home_team_id(conn, game_id) else "away"
        by_side[side].append(f"{row['player']} {impact:.1f}")
        points[side] += impact
    # 分值闸门（老规则）：有正分缺阵才可能触发
    for edge in edges:
        if not edge.get("bettable"):
            continue
        side = side_of(edge)
        if side is None or points.get(side, 0.0) < INJURY_GATE_POINTS:
            continue
        team = _team_short(conn, _home_team_id(conn, game_id) if side == "home" else _away_team_id(conn, game_id))
        names = ", ".join(sorted(by_side[side], reverse=True))
        note = f"伤停闸门：{team} 缺人合计 {points[side]:.1f} 分（{names}）"
        edge["bettable"] = False
        edge["bettable_note"] = note
        edge["gated"] = note

    # ---------- 盲区闸门（0.99）：未评分缺阵 ----------
    # 既有闸门 `if impact <= 0: continue` 对 impact=0 的 out/doubtful 全盲。
    # 未评分缺阵分两档（manual 手设 0 分 = 人工确认无影响，不进盲区）：
    # - 打分失败：本赛季有出场痕迹（players 表有行）但 RAPM 无建议 → 1 个即拦；
    # - 无法解析：players 表无行，无法核实是否角色球员 → ≥2 个才拦。
    # 大小分（side=None）两侧任一触发即拦：缺核心得分手时模型总分口径失真，
    # 大小两个方向的 edge 都不可信，保守双杀。
    if not INJURY_GATE_ENABLE:
        return edges
    home_id = _home_team_id(conn, game_id)
    unscored_played: dict[str, list[str]] = {"home": [], "away": []}
    unscored_unknown: dict[str, list[str]] = {"home": [], "away": []}
    for row in rows:
        status = (row["status"] or "").casefold()
        if status not in _GATE_STATUSES:
            continue
        source = row.get("impact_source") or "auto"
        if source == "manual":
            # 人工确认的 0 分 = 真没影响，不进盲区；manual 正分已被主循环处理
            continue
        sug = suggestions.get((row["team_id"], row["player"]))
        if sug is not None:
            # 有建议（哪怕建议值 0 = RAPM 评分为负钳到 0）→ 打分流程正常走过
            continue
        if float(row["impact"] or 0.0) > 0:
            continue  # 有正分已被主循环处理
        side = "home" if row["team_id"] == home_id else "away"
        played = _season_appearance(conn, row["player"])
        if played:
            unscored_played[side].append(row["player"])
        elif played is None:
            unscored_unknown[side].append(row["player"])

    for edge in edges:
        if not edge.get("bettable") or edge.get("gated"):
            continue  # 已被分值闸门拦的不重复标注
        side = side_of(edge)
        sides = [side] if side else ["home", "away"]
        reason = None
        for s in sides:
            team_id = home_id if s == "home" else _away_team_id(conn, game_id)
            team = _team_short(conn, team_id) if team_id else s
            if len(unscored_played[s]) >= INJURY_GATE_UNSCORED_PLAYED:
                names = ", ".join(sorted(set(unscored_played[s])))
                reason = (
                    f"伤停闸门（盲区）：{team} 缺阵未评分但有出场痕迹（{names}）"
                    "——影响分打分失败，模型按满员计算，保守拦截"
                )
                break
            if len(unscored_unknown[s]) >= INJURY_GATE_UNRESOLVED:
                names = ", ".join(sorted(set(unscored_unknown[s])))
                reason = (
                    f"伤停闸门（盲区）：{team} 有 {len(unscored_unknown[s])} 名无法核实的缺阵"
                    f"（{names}）——影响分缺失，保守拦截"
                )
                break
        if reason:
            edge["bettable"] = False
            edge["bettable_note"] = reason
            edge["gated"] = reason
    return edges


def _home_team_id(conn: sqlite3.Connection, game_id: str) -> int | None:
    row = conn.execute("SELECT home_id FROM games WHERE id=?", (str(game_id),)).fetchone()
    return int(row["home_id"]) if row else None


def _away_team_id(conn: sqlite3.Connection, game_id: str) -> int | None:
    row = conn.execute("SELECT away_id FROM games WHERE id=?", (str(game_id),)).fetchone()
    return int(row["away_id"]) if row else None
