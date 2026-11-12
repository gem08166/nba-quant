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

import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path

import httpx

from ..config import DATA_DIR, REQUEST_SLEEP, REQUEST_TIMEOUT

API = "https://hoopsapi.com/api/v1"

# 配额：每滚动 24 小时 10 个请求（请求发出时开始计时）。超限返回 429 + 重置时间头。
# 预算策略：列表请求 1 次/天，其余留给归档（优先归档开球最早的完赛场）；
# 单次调用最多用掉 4 个请求，剩余留给后续 watch 循环（5 分钟一轮）分摊。
REQUEST_BUDGET_24H = 10
MAX_REQUESTS_PER_INVOCATION = 4
STATE_PATH = DATA_DIR / "hoops_state.json"

# 最小调用间隔：watch 每 10 分钟一轮，若不设间隔会在 100 分钟内把 10 次预算打光，
# 之后整天都拿不到（Pinnacle 收盘价反而缺失）。默认 150 分钟 ≈ 每天 9–10 次，
# 正好把预算摊到全天。本机想更省（和 CI 共用同一个 key）可以调大。
MIN_INTERVAL_S = float(os.getenv("NBAQ_HOOPS_MIN_INTERVAL_MIN", "150")) * 60


class HoopsRateLimited(RuntimeError):
    """24h 预算用尽或收到 429；应推迟到下一轮再试。"""

    def __init__(self, message: str, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class HoopsError(RuntimeError):
    pass


def _load_state() -> dict:
    try:
        return json.loads(Path(STATE_PATH).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    Path(STATE_PATH).write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _available_requests(state: dict) -> int:
    now = time.time()
    stamps = [t for t in state.get("requests", []) if now - t < 86400]
    state["requests"] = stamps
    return REQUEST_BUDGET_24H - len(stamps)


def _headers() -> dict:
    from ..config import HOOPS_API_KEY

    if not HOOPS_API_KEY:
        raise HoopsError("未配置 HOOPS_API_KEY")
    return {"Authorization": f"Bearer {HOOPS_API_KEY}"}


def _sleep() -> None:
    time.sleep(REQUEST_SLEEP)


def _call(client: httpx.Client, url: str, params: dict | None = None) -> dict:
    """带预算管理的 GET：滚动 24h 窗口计数、429 退避。"""
    state = _load_state()
    if _available_requests(state) <= 0:
        raise HoopsRateLimited("HoopsAPI 24h 预算已用完，推迟到下一轮")
    if state.get("rate_limited_until") and time.time() < state["rate_limited_until"]:
        raise HoopsRateLimited(f"429 退避中，{state['rate_limited_until'] - time.time():.0f}s 后可用")
    resp = client.get(url, params=params, headers=_headers())
    if resp.status_code == 429:
        retry_after = None
        for header in ("x-ratelimit-reset", "x-rate-limit-reset", "retry-after"):
            value = resp.headers.get(header)
            if value:
                try:
                    retry_after = float(value)
                    # retry-after 可能是绝对时间戳或秒数：秒数很大时按绝对时间戳处理
                    if retry_after > 1e12:
                        retry_after = retry_after / 1000.0 - time.time()
                except ValueError:
                    retry_after = None
        state["rate_limited_until"] = time.time() + (retry_after if retry_after and 0 < retry_after < 86400 else 900)
        _save_state(state)
        raise HoopsRateLimited(f"429，{state['rate_limited_until'] - time.time():.0f}s 后重试", retry_after)
    resp.raise_for_status()
    state.setdefault("requests", []).append(time.time())
    _save_state(state)
    return resp.json()


def fetch_nba_games(client: httpx.Client) -> list[dict]:
    resp = _call(client, f"{API}/games", {"competition": "NBA"})
    return resp.get("games") or []


def fetch_game(game_id, client: httpx.Client) -> dict:
    return _call(client, f"{API}/games/{game_id}")


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
    """每日同步：抓 NBA upcoming 列表 → 建立 hoops↔espn 映射 + 主盘入库。

    受最小调用间隔约束：watch 每 10 分钟一轮，若不限流会在 100 分钟内把
    10 次/24h 预算打光，之后整天拿不到（Pinnacle 收盘价反而缺失）。
    """
    from . import service as espn_service

    state = _load_state()
    last = float(state.get("last_sync_ts") or 0)
    if last and time.time() - last < MIN_INTERVAL_S:
        waited = (time.time() - last) / 60
        return {
            "games": 0,
            "mapped": 0,
            "odds_rows": 0,
            "skipped": f"距上次 {waited:.0f}min < {MIN_INTERVAL_S / 60:.0f}min",
        }

    stats = {"games": 0, "mapped": 0, "odds_rows": 0}
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        games = fetch_nba_games(client)
        _sleep()
    stats["games"] = len(games)
    state = _load_state()
    state["last_sync_ts"] = time.time()
    _save_state(state)

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
    """对已完赛且未归档的映射比赛，从 HoopsAPI 归档拉多书收盘价入库。

    受 10 请求/24h 预算约束：单次调用最多 MAX_REQUESTS_PER_INVOCATION 个请求，
    优先归档开球最早的（收盘价最有研究价值）；预算耗尽抛 HoopsRateLimited，
    由 watch 下一轮（5 分钟后）继续。
    """
    from . import service as espn_service

    stats = {"archived": 0, "odds_rows": 0, "errors": 0, "deferred": 0}
    pending = conn.execute(
        """
        SELECT hg.hoops_id, hg.espn_game_id, g.status, g.tipoff FROM hoops_games hg
        JOIN games g ON g.id = hg.espn_game_id
        WHERE hg.archived = 0
        ORDER BY g.tipoff
        """
    ).fetchall()
    finished = [row for row in pending if row["status"] == "STATUS_FINAL"]
    stats["deferred"] = len(pending) - len(finished)
    if not finished:
        return stats

    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        for row in finished[:MAX_REQUESTS_PER_INVOCATION]:
            try:
                detail = fetch_game(row["hoops_id"], client)
                _sleep()
                rows = _providers_to_rows(detail.get("providers"))
                espn_service.insert_odds(conn, row["espn_game_id"], rows)
                conn.execute("UPDATE hoops_games SET archived=1 WHERE hoops_id=?", (row["hoops_id"],))
                conn.commit()
                stats["archived"] += 1
                stats["odds_rows"] += len(rows)
            except HoopsRateLimited as exc:
                stats["deferred"] += 1
                print(f"[hoops] 预算/限流：{exc}，剩余归档推迟")
                break
            except Exception:  # noqa: BLE001 - 单场失败不拖垮
                stats["errors"] += 1
    return stats


def _ts(value: str) -> float:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return 0.0
