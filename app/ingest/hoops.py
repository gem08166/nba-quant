from __future__ import annotations

"""HoopsAPI（hoopsapi.com）多书盘口客户端。

书源：1xbet / betway / fanduel / **pinnacle** / smarkets / unibet（10 分钟刷新）。
市场：moneyline / spread(主队让球线) / total / team totals；period: ft/h1/h2/q1-q4。

对研究最有价值的两点：
1. **Pinnacle 收盘线**——业内最强基准，模型 CLV 的黄金标准；
2. **已完赛比赛从归档返回收盘价**——按前向积累：每日抓 upcoming 建立映射
   （hoops_games 表），完赛后用 hoops_id 从归档拉多书收盘价入库
   odds_snapshots（book=书名），喂给"模型 vs 收盘共识"回测。
"""

import sqlite3
import time
from datetime import datetime, timezone

import httpx

from ..config import REQUEST_SLEEP, REQUEST_TIMEOUT

API = "https://hoopsapi.com/api/v1"


class HoopsError(RuntimeError):
    pass


def _headers() -> dict:
    from ..config import HOOPS_API_KEY

    if not HOOPS_API_KEY:
        raise HoopsError("未配置 HOOPS_API_KEY")
    return {"Authorization": f"Bearer {HOOPS_API_KEY}"}


def _sleep() -> None:
    time.sleep(REQUEST_SLEEP)


def fetch_nba_games(client: httpx.Client) -> list[dict]:
    resp = client.get(f"{API}/games", params={"competition": "NBA"}, headers=_headers())
    resp.raise_for_status()
    return resp.json().get("games") or []


def fetch_game(game_id, client: httpx.Client) -> dict:
    resp = client.get(f"{API}/games/{game_id}", headers=_headers())
    resp.raise_for_status()
    return resp.json()


def _providers_to_rows(providers: dict) -> list[dict]:
    """providers.{book}.{moneyline|handicap|over_under} → odds_snapshots rows。"""
    rows: list[dict] = []
    for book, block in (providers or {}).items():
        ml = block.get("moneyline") or {}
        if ml.get("home") and ml.get("away"):
            rows.append({"book": book, "market": "moneyline", "side": "home", "line": None, "price": float(ml["home"]), "source": "hoops"})
            rows.append({"book": book, "market": "moneyline", "side": "away", "line": None, "price": float(ml["away"]), "source": "hoops"})
        hdp = block.get("handicap") or {}
        if hdp.get("line") is not None and hdp.get("home") and hdp.get("away"):
            line = float(hdp["line"])
            rows.append({"book": book, "market": "spread", "side": "home", "line": line, "price": float(hdp["home"]), "source": "hoops"})
            rows.append({"book": book, "market": "spread", "side": "away", "line": -line, "price": float(hdp["away"]), "source": "hoops"})
        ou = block.get("over_under") or {}
        if ou.get("line") is not None and ou.get("over") and ou.get("under"):
            line = float(ou["line"])
            rows.append({"book": book, "market": "total", "side": "over", "line": line, "price": float(ou["over"]), "source": "hoops"})
            rows.append({"book": book, "market": "total", "side": "under", "line": line, "price": float(ou["under"]), "source": "hoops"})
    return rows


def sync_games(conn: sqlite3.Connection) -> dict:
    """每日同步：抓 NBA upcoming 列表 → 建立 hoops↔espn 映射 + 主盘入库。"""
    from . import service as espn_service

    stats = {"games": 0, "mapped": 0, "odds_rows": 0}
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        games = fetch_nba_games(client)
        _sleep()
    stats["games"] = len(games)

    for game in games:
        home, away = game.get("home") or "", game.get("away") or ""
        start = game.get("start_time") or ""
        # 匹配本库比赛：队名（双向包含）+ 开球时间 ±10h（跨时区容差）
        row = conn.execute(
            """
            SELECT g.id, g.tipoff FROM games g
            JOIN teams h ON h.id=g.home_id JOIN teams a ON a.id=g.away_id
            WHERE (h.name=? OR h.name LIKE ?) AND (a.name=? OR a.name LIKE ?)
              AND g.status NOT IN ('STATUS_FINAL')
            """,
            (home, f"%{home}%", away, f"%{away}%"),
        ).fetchall()
        # 多场候选时用开球时间最近的一场
        best, best_gap = None, None
        for candidate in row:
            tip = candidate["tipoff"] or ""
            gap = abs(_ts(tip) - _ts(start)) if tip and start else 1e18
            if best_gap is None or gap < best_gap:
                best, best_gap = candidate, gap
        if best is None or best_gap > 10 * 3600:
            continue
        espn_id = best["id"]
        conn.execute(
            """
            INSERT INTO hoops_games(hoops_id, espn_game_id, start_time, archived)
            VALUES(?, ?, ?, 0)
            ON CONFLICT(hoops_id) DO UPDATE SET espn_game_id=excluded.espn_game_id
            """,
            (str(game["game_id"]), espn_id, start),
        )
        stats["mapped"] += 1
        stats["odds_rows"] += espn_service.insert_odds(conn, espn_id, _providers_to_rows(game.get("providers")))
    conn.commit()
    return stats


def archive_closing(conn: sqlite3.Connection) -> dict:
    """对已完赛且未归档的映射比赛，从 HoopsAPI 归档拉多书收盘价入库。"""
    from . import service as espn_service

    stats = {"archived": 0, "odds_rows": 0, "errors": 0}
    pending = conn.execute(
        """
        SELECT hg.hoops_id, hg.espn_game_id, g.status FROM hoops_games hg
        JOIN games g ON g.id = hg.espn_game_id
        WHERE hg.archived = 0
        """
    ).fetchall()
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        for row in pending:
            if row["status"] != "STATUS_FINAL":
                continue
            try:
                detail = fetch_game(row["hoops_id"], client)
                _sleep()
                rows = _providers_to_rows(detail.get("providers"))
                espn_service.insert_odds(conn, row["espn_game_id"], rows)
                conn.execute("UPDATE hoops_games SET archived=1 WHERE hoops_id=?", (row["hoops_id"],))
                stats["archived"] += 1
                stats["odds_rows"] += len(rows)
            except Exception:  # noqa: BLE001 - 单场失败不拖垮
                stats["errors"] += 1
    conn.commit()
    return stats


def _ts(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0
