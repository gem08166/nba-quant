from __future__ import annotations

"""The Odds API 客户端（多书源共识）。

额度非常有限（免费 500 credits/月），默认策略：
- 每天最多拉 1 次（h2h+spreads+totals × 1 region = 3 credits）≈ 90/月
- 状态文件 data/odds_api_state.json 记录上次拉取时间与剩余额度，间隔不足直接跳过
- 每次响应打印 x-requests-remaining

定价：一次请求返回多家 bookmaker（DraftKings/FanDuel/...），全部入库
odds_snapshots（book=书名，source='the-odds-api'），供共识概率与交叉验证。
"""

import json
import time
from datetime import datetime, timezone

import httpx

from ..config import (
    DATA_DIR,
    ODDS_CLOSE_WINDOW_HOURS,
    REQUEST_TIMEOUT,
    THE_ODDS_API_KEYS,
    ODDS_CLOSE_FETCH_MIN_HOURS,
    THE_ODDS_API_MIN_HOURS,
)
from ..timeutil import eastern_date

API_BASE = "https://api.the-odds-api.com/v4"
SPORT_NBA = "basketball_nba"
REGION = "us"
MARKETS = "h2h,spreads,totals"
STATE_PATH = DATA_DIR / "odds_api_state.json"


class OddsApiError(RuntimeError):
    pass


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _last_fetch_from_db() -> float | None:
    """状态文件缺失时（CI 只还原 nba.db，不带 state json），退回读库里的快照时间。
    否则 CI 每轮都会认为「首次调用」，30 分钟烧 6 点额度。"""
    import sqlite3
    from ..config import DB_PATH
    try:
        conn = sqlite3.connect(DB_PATH, timeout=10)
        row = conn.execute(
            "SELECT MAX(captured_at) FROM odds_snapshots WHERE source = 'the-odds-api'"
        ).fetchone()
        conn.close()
    except Exception:
        return None
    if not row or not row[0]:
        return None
    try:
        ts = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
    except ValueError:
        return None
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return ts.timestamp()


def hours_since_last_fetch() -> float | None:
    state = _load_state()
    last = state.get("last_fetch_ts")
    if not last:
        last = _last_fetch_from_db()
    if not last:
        return None
    return (time.time() - last) / 3600.0


def _near_closing_window() -> bool:
    """未来 ODDS_CLOSE_WINDOW_HOURS 小时内是否有开赛——收盘捕捉期豁免间隔闸门。"""
    import sqlite3

    try:
        conn = sqlite3.connect(DATA_DIR / "nba.db")
        rows = conn.execute(
            "SELECT tipoff FROM games WHERE status != 'STATUS_FINAL' AND tipoff IS NOT NULL"
        ).fetchall()
        conn.close()
    except Exception:  # noqa: BLE001 - 查不到就按不在窗口处理
        return False
    now = datetime.now(timezone.utc)
    for (tipoff,) in rows:
        try:
            tip = datetime.fromisoformat(str(tipoff).replace("Z", "+00:00"))
        except ValueError:
            continue
        if tip.tzinfo is None:
            tip = tip.replace(tzinfo=timezone.utc)
        hours_to_tip = (tip - now).total_seconds() / 3600.0
        if 0 <= hours_to_tip <= ODDS_CLOSE_WINDOW_HOURS:
            return True
    return False


def _remaining_quota_ok() -> tuple[bool, str]:
    hrs = hours_since_last_fetch()
    if hrs is None:
        return True, "首次拉取"
    if hrs < THE_ODDS_API_MIN_HOURS:
        if _near_closing_window():
            # 收盘窗口：豁免 4h 长间隔，但保持 15 分钟最小节奏（省额度且够密）
            if hrs < ODDS_CLOSE_FETCH_MIN_HOURS:
                return False, (
                    f"收盘窗口内 {hrs * 60:.0f}min < {ODDS_CLOSE_FETCH_MIN_HOURS * 60:.0f}min"
                    "（15 分钟一轮）"
                )
            return True, f"收盘窗口内拉取（{hrs * 60:.0f}min ≥ {ODDS_CLOSE_FETCH_MIN_HOURS * 60:.0f}min）"
        return False, f"距上次拉取 {hrs:.1f}h < {THE_ODDS_API_MIN_HOURS}h（省额度，跳过）"
    return True, f"距上次拉取 {hrs:.1f}h"



def fetch_nba_odds(force: bool = False) -> tuple[list[dict], str]:
    """拉取 NBA 未来赛程的多家盘口。返回 (events, message)。

    events: [{id, commence_time, home, away, bookmakers: [{title, markets...}]}]
    额度不足/间隔不足时返回 ([], reason)。
    """
    if not THE_ODDS_API_KEYS:
        return [], "未配置 NBAQ_ODDS_API_KEY，跳过"
    if not force:
        ok, reason = _remaining_quota_ok()
        if not ok:
            return [], reason

    state = _load_state()
    key_idx = int(state.get("key_idx") or 0) % len(THE_ODDS_API_KEYS)
    events = None
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        for attempt in range(len(THE_ODDS_API_KEYS)):
            key = THE_ODDS_API_KEYS[(key_idx + attempt) % len(THE_ODDS_API_KEYS)]
            resp = client.get(
                f"{API_BASE}/sports/{SPORT_NBA}/odds/",
                params={
                    "apiKey": key,
                    "regions": REGION,
                    "markets": MARKETS,
                    "oddsFormat": "decimal",
                },
            )
            remaining = resp.headers.get("x-requests-remaining")
            used = resp.headers.get("x-requests-used")
            if resp.status_code == 429 and attempt < len(THE_ODDS_API_KEYS) - 1:
                # 当前 key 额度尽 → 记录并轮换到下一个 key 重试
                _save_state({**_load_state(), "key_idx": (key_idx + attempt + 1) % len(THE_ODDS_API_KEYS)})
                continue
            if resp.status_code == 429:
                _save_state({**_load_state(), "last_fetch_ts": time.time()})
                return [], f"全部 key 额度用尽（429），remaining={remaining}"
            resp.raise_for_status()
            events = resp.json()
            key_idx = (key_idx + attempt) % len(THE_ODDS_API_KEYS)
            _save_state({**_load_state(), "key_idx": key_idx})
            break
    if events is None:
        return [], "未获取到盘口数据"

    # 用最新磁盘状态做基线，避免覆盖掉轮换循环里写入的 key_idx
    state = _load_state()
    state.update(
        {
            "last_fetch_ts": time.time(),
            "remaining": remaining,
            "used": used,
            "events": len(events),
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        }
    )
    _save_state(state)
    msg = f"The Odds API: {len(events)} 场 · 剩余额度 {remaining}（已用 {used}）"
    print(msg)
    return events, msg


def ingest_events(conn, events: list[dict], slate_date: str | None = None) -> int:
    """把 events（The Odds API 形状）按队名匹配比赛后入库。

    手机采集端推送的快照 JSON 也用这个形状，PC 端复用同一套匹配+入库。
    """
    rows_by_key = events_to_rows(events)
    inserted = 0
    for event in events:
        home = event.get("home_team") or ""
        away = event.get("away_team") or ""
        key = f"{away}|{home}"
        kickoff = event.get("commence_time")  # ISO UTC
        try:
            et_slate = eastern_date(kickoff)
        except (TypeError, ValueError):
            continue
        if slate_date and et_slate != slate_date:
            continue
        game = conn.execute(
            """
            SELECT g.id FROM games g
            JOIN teams h ON h.id=g.home_id JOIN teams a ON a.id=g.away_id
            WHERE h.name=? AND a.name=? AND g.slate_date=?
              AND g.status NOT IN ('STATUS_POSTPONED','STATUS_CANCELED','STATUS_CANCELLED')
            """,
            (home, away, et_slate),
        ).fetchone()
        if not game:
            continue
        inserted += insert_api_rows(conn, game["id"], rows_by_key.get(key) or [])
    return inserted


def store_odds_api(conn, slate_date: str | None = None, force: bool = False) -> str:
    """The Odds API 多书源盘口入库（book=bookmaker 名），返回状态消息。

    队名匹配：Odds API 与 ESPN 均为英文全名；日期口径同 hao（UTC-5 = 美国比赛日）。
    """
    events, message = fetch_nba_odds(force=force)
    if not events:
        return message

    inserted = ingest_events(conn, events, slate_date=slate_date)
    return f"{message} · 入库 {inserted} 行"


def insert_api_rows(conn, game_id: str, rows: list[dict]) -> int:
    from . import service as espn_service

    return espn_service.insert_odds(conn, game_id, rows)


def events_to_rows(events: list[dict]) -> dict[str, list[dict]]:
    """把响应转成 {队名对: [odds_snapshots rows]}（book=bookmaker 名）。"""
    out: dict[str, list[dict]] = {}
    for event in events:
        home = event.get("home_team") or ""
        away = event.get("away_team") or ""
        key = f"{away}|{home}"
        rows: list[dict] = []
        for bookmaker in event.get("bookmakers") or []:
            book = bookmaker.get("title") or "unknown"
            for market in bookmaker.get("markets") or []:
                mkey = market.get("key")
                outcomes = market.get("outcomes") or []
                if mkey == "h2h":
                    for outcome in outcomes:
                        side = "home" if outcome.get("name") == home else "away" if outcome.get("name") == away else None
                        if side:
                            rows.append(
                                {"book": book, "market": "moneyline", "side": side, "line": None,
                                 "price": float(outcome.get("price")), "source": "the-odds-api"}
                            )
                elif mkey == "spreads":
                    for outcome in outcomes:
                        side = "home" if outcome.get("name") == home else "away" if outcome.get("name") == away else None
                        if side and outcome.get("point") is not None:
                            rows.append(
                                {"book": book, "market": "spread", "side": side, "line": float(outcome["point"]),
                                 "price": float(outcome.get("price")), "source": "the-odds-api"}
                            )
                elif mkey == "totals":
                    for outcome in outcomes:
                        side = "over" if outcome.get("name") == "Over" else "under" if outcome.get("name") == "Under" else None
                        if side and outcome.get("point") is not None:
                            rows.append(
                                {"book": book, "market": "total", "side": side, "line": float(outcome["point"]),
                                 "price": float(outcome.get("price")), "source": "the-odds-api"}
                            )
        out[key] = rows
    return out
