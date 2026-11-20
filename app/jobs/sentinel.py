"""夜间哨兵数据通道（VPS → 坚果云 → 本机）。

背景（2026-11-21）：用户睡觉时本机关机，VPS 只跑干净源（ESPN + The Odds API）
继续采集与推送；333/hao 这类博彩域名不上 VPS（国内云合规审计会看 TLS SNI）。

数据流向：
    VPS  sentinel.export_day()  →  nbaq/sentinel/<slate>.json（WebDAV）
    本机 sentinel.pull_recent() ←  上述文件，按唯一索引幂等合并

合并规则：
    games          —— 按 updated_at 新者胜（VPS 的比分/状态可能更新）
    odds_snapshots —— INSERT OR IGNORE（唯一索引含全部字段，天然去重）
    injuries       —— INSERT OR IGNORE（唯一索引 game/team/player/status）
    predictions    —— 不同步：本机早晨会用最新盘口重算，夜间快照无下注价值

只同步「夜间时段」的数据：导出窗口 = 最近 48 小时，避免文件无限增长。
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from . import webdav

# 导出/拉取窗口（小时）：VPS 只保留最近两天的快照
WINDOW_HOURS = 48
# 本机开机时回看几天（覆盖周末没开机的情况）
PULL_DAYS = 3

GAME_COLS = (
    "id", "season", "season_type", "slate_date", "tipoff", "home_id", "away_id",
    "home_score", "away_score", "status", "status_detail", "venue", "updated_at",
    "live_home_score", "live_away_score",
)
ODDS_COLS = (
    "game_id", "book", "market", "side", "line", "price", "captured_at", "source",
    "last_seen_at",
)
INJ_COLS = (
    "game_id", "team_id", "player", "status", "detail", "impact", "captured_at",
    "impact_source", "applied_status", "applied_at",
)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def _slate_dates(slate: str) -> list[str]:
    day = datetime.fromisoformat(slate)
    return [(day + timedelta(days=d)).date().isoformat() for d in (-1, 0, 1)]


def build_payload(conn, slate: str, now: datetime | None = None) -> dict:
    """组装某个 slate 的导出包（games + 近 48h 的 odds/injuries）。"""
    now = now or datetime.now(timezone.utc)
    since = _iso(now - timedelta(hours=WINDOW_HOURS))
    slates = _slate_dates(slate)
    marks = ",".join("?" * len(slates))
    games = [
        {c: r[c] for c in GAME_COLS}
        for r in conn.execute(f"SELECT * FROM games WHERE slate_date IN ({marks})", slates)
    ]
    if not games:
        return {"slate": slate, "exported_at": _iso(now), "games": [], "odds": [], "injuries": []}
    gids = [g["id"] for g in games]
    gmarks = ",".join("?" * len(gids))
    odds = [
        {c: r[c] for c in ODDS_COLS}
        for r in conn.execute(
            f"SELECT * FROM odds_snapshots WHERE game_id IN ({gmarks}) AND captured_at >= ?",
            (*gids, since),
        )
    ]
    injuries = [
        {c: r[c] for c in INJ_COLS}
        for r in conn.execute(
            f"SELECT * FROM injuries WHERE game_id IN ({gmarks}) AND captured_at >= ?",
            (*gids, since),
        )
    ]
    return {
        "slate": slate,
        "exported_at": _iso(now),
        "games": games,
        "odds": odds,
        "injuries": injuries,
    }


def export_day(conn, slate: str, dry_run_path=None) -> dict:
    """导出到 WebDAV（或本地文件，测试用）。返回统计。"""
    payload = build_payload(conn, slate)
    text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    stats = {
        "slate": slate,
        "games": len(payload["games"]),
        "odds": len(payload["odds"]),
        "injuries": len(payload["injuries"]),
        "bytes": len(text.encode("utf-8")),
        "uploaded": False,
    }
    if dry_run_path is not None:
        from pathlib import Path

        Path(dry_run_path).write_text(text, encoding="utf-8")
        stats["file"] = str(dry_run_path)
        return stats
    if not payload["games"]:
        return stats
    stats["uploaded"] = webdav.put_text(f"{webdav.SENTINEL}/{slate}.json", text)
    return stats


def _merge(conn, payload: dict) -> dict:
    """把导出包幂等合并进本地库。

    odds 的唯一索引含 line/price，而 moneyline 的 line 是 NULL——SQLite 的
    UNIQUE 对 NULL 不去重（NULL != NULL），所以不能用 INSERT OR IGNORE：
    先按 (game,book,market,side,line,price,captured_at) 预载已有键，再逐条判断。
    """
    out = {"games_new": 0, "games_updated": 0, "odds_new": 0, "injuries_new": 0}
    games = payload.get("games") or []
    odds = payload.get("odds") or []
    injuries = payload.get("injuries") or []

    # --- games：按 updated_at 新者胜 ---
    for g in games:
        row = conn.execute("SELECT updated_at FROM games WHERE id=?", (g["id"],)).fetchone()
        if row is None:
            conn.execute(
                f"INSERT INTO games ({','.join(GAME_COLS)}) VALUES ({','.join('?' * len(GAME_COLS))})",
                tuple(g.get(c) for c in GAME_COLS),
            )
            out["games_new"] += 1
        elif (g.get("updated_at") or "") > (row["updated_at"] or ""):
            sets = ",".join(f"{c}=?" for c in GAME_COLS if c != "id")
            conn.execute(
                f"UPDATE games SET {sets} WHERE id=?",
                (*[g.get(c) for c in GAME_COLS if c != "id"], g["id"]),
            )
            out["games_updated"] += 1

    # --- odds：预载已有键（含 NULL 语义） ---
    gids = sorted({o["game_id"] for o in odds})
    existing = set()
    if gids:
        marks = ",".join("?" * len(gids))
        for r in conn.execute(
            "SELECT game_id, book, market, side, line, price, captured_at "
            f"FROM odds_snapshots WHERE game_id IN ({marks})",
            gids,
        ):
            existing.add((r["game_id"], r["book"], r["market"], r["side"],
                          r["line"], r["price"], r["captured_at"]))
    for o in odds:
        key = (o["game_id"], o["book"], o["market"], o["side"],
               o.get("line"), o.get("price"), o["captured_at"])
        if key in existing:
            continue
        conn.execute(
            f"INSERT INTO odds_snapshots ({','.join(ODDS_COLS)}) "
            f"VALUES ({','.join('?' * len(ODDS_COLS))})",
            tuple(o.get(c) for c in ODDS_COLS),
        )
        existing.add(key)
        out["odds_new"] += 1

    # --- injuries：唯一键 (game,team,player,status)，冲突时更新 detail/captured_at ---
    igids = sorted({i["game_id"] for i in injuries})
    known = set()
    if igids:
        marks = ",".join("?" * len(igids))
        for r in conn.execute(
            "SELECT game_id, team_id, player, status "
            f"FROM injuries WHERE game_id IN ({marks})",
            igids,
        ):
            known.add((r["game_id"], r["team_id"], r["player"], r["status"]))
    for i in injuries:
        key = (i["game_id"], i["team_id"], i["player"], i.get("status"))
        if key in known:
            conn.execute(
                "UPDATE injuries SET detail=?, captured_at=? "
                "WHERE game_id=? AND team_id=? AND player=? AND status IS ?",
                (i.get("detail"), i.get("captured_at"), *key),
            )
            continue
        conn.execute(
            f"INSERT INTO injuries ({','.join(INJ_COLS)}) "
            f"VALUES ({','.join('?' * len(INJ_COLS))})",
            tuple(i.get(c) for c in INJ_COLS),
        )
        known.add(key)
        out["injuries_new"] += 1
    return out


def pull_recent(conn, today: str, days: int = PULL_DAYS) -> dict:
    """拉取最近 N 天的哨兵导出并合并。返回汇总。"""
    total = {"files": 0, "games_new": 0, "games_updated": 0, "odds_new": 0, "injuries_new": 0}
    day0 = datetime.fromisoformat(today)
    for d in range(days):
        slate = (day0 - timedelta(days=d)).date().isoformat()
        try:
            text = webdav.get_text(f"{webdav.SENTINEL}/{slate}.json")
        except Exception as exc:  # noqa: BLE001
            total.setdefault("errors", []).append(f"{slate}: {exc}")
            continue
        if not text:
            continue
        payload = json.loads(text)
        merged = _merge(conn, payload)
        total["files"] += 1
        for k, v in merged.items():
            total[k] += v
    return total
