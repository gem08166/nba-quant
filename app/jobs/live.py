from __future__ import annotations

"""Live (in-game) board.

Sources (all free ESPN, no cdn.nba.com):
- scoreboard per date: live scores, period, clock seconds, state (pre/in/post)
- summary per live game: per-play homeWinPercentage curve + recent plays

Live projection model: with `f` = fraction of regulation played,
  projected margin = current_margin + pregame_margin * (1-f)
  projected total  = current_total  + pregame_total  * (1-f)
  sigma scales by sqrt(1-f) (uncertainty shrinks as the game advances).
OT periods are treated as extra time beyond regulation (f capped at 1 for
scoring projection, but sigma keeps shrinking via played_seconds).
"""

import json
import sqlite3
import time
from collections import Counter
from datetime import datetime, timezone

import httpx

from ..config import ESPN_BASE, REQUEST_SLEEP, REQUEST_TIMEOUT, USER_AGENT
from ..pricing.devig import cover_prob, moneyline_prob, over_prob
from .predict import latest_prediction
from .signal_backtest import settle_edge
from ..models.live_wp import seconds_remaining as wp_seconds_remaining
from ..models.live_wp import win_prob as wp_win_prob
from .. import i18n

PERIOD_SECONDS = 720
OT_SECONDS = 300
REGULATION_SECONDS = 4 * PERIOD_SECONDS
LIVE_STATES = {"in"}

# 滚球盘口快照超过这个秒数就视为"不新鲜"，不再当作可下单价格展示
LIVE_MARKET_STALE_SECONDS = 300

# --- 信号历史（赛后板块用）--------------------------
# "曾经出现的信号" = 该场全部历史 prediction 版本里 signal=True 的边缘，
# 按 (市场, 方向, 线) 去重合并。/api/live 每 30s 被轮询（本机+手机），
# 全量扫 JSON 太重 → 按 game_id 缓存，TTL 10 分钟。
_SIGNAL_CACHE: dict[str, dict] = {}
_SIGNAL_CACHE_TTL = 600.0


def merge_signal_versions(rows) -> list[dict]:
    """合并同一信号在历史 prediction 里的多次出现。

    rows: [(created_at, details_json), ...] 按 id 升序。
    同 (market, side, line) 只保留一条，记录：最高 EV / 最高 edge、
    最优价（亚盘口赔率越高对投注者越有利）、首次/最近出现时间、
    出现次数、是否曾可下注/曾被分歧闸门拦下。
    """
    groups: dict[tuple, dict] = {}
    for created_at, details_json in rows:
        try:
            details = json.loads(details_json)
        except (TypeError, ValueError):
            continue
        for edge in details.get("edges") or []:
            if not edge.get("signal"):
                continue
            key = (edge.get("market"), edge.get("side"), edge.get("line"))
            ev = edge.get("ev") or 0.0
            edge_val = edge.get("edge") or 0.0
            price = edge.get("price")
            group = groups.get(key)
            if group is None:
                groups[key] = {
                    "market": edge.get("market"),
                    "side": edge.get("side"),
                    "line": edge.get("line"),
                    "book": edge.get("book"),
                    "model_prob": edge.get("model_prob"),
                    "fair_prob": edge.get("fair_prob"),
                    "ev_max": ev,
                    "edge_max": edge_val,
                    "price_best": price,
                    "price_last": price,
                    "first_seen": created_at,
                    "last_seen": created_at,
                    "n_seen": 1,
                    "bettable_ever": bool(edge.get("bettable")),
                    "gated_ever": bool(edge.get("gated")),
                    "reasons": Counter(),
                    "n_bettable": 0,
                    "schema_old": False,
                }
            else:
                group["ev_max"] = max(group["ev_max"], ev)
                group["edge_max"] = max(group["edge_max"], edge_val)
                if price and group["price_best"]:
                    group["price_best"] = max(group["price_best"], price)
                group["price_last"] = price or group["price_last"]
                group["n_seen"] += 1
                group["bettable_ever"] = group["bettable_ever"] or bool(edge.get("bettable"))
                group["gated_ever"] = group["gated_ever"] or bool(edge.get("gated"))
                if created_at:
                    group["last_seen"] = created_at
            # 阀门口径（复盘页标注用）：
            g = groups[key]
            if "bettable" in edge:
                if edge["bettable"]:
                    g["n_bettable"] += 1
                elif edge.get("bettable_note"):
                    g["reasons"][edge["bettable_note"][:46]] += 1
            else:
                g["schema_old"] = True  # 11-04 闸门上线前的旧预测，无法判定
    # 复盘页标注：这组信号在当时到底能不能下（以及不能下的原因）
    for g in groups.values():
        g["bettable_label"] = _bettable_label(g)
    signals = sorted(groups.values(), key=lambda g: g["ev_max"], reverse=True)
    return signals[:10]  # 上限防极端（正常每场 <5 条）


def _bettable_label(g: dict) -> str:
    """复盘标注：可下注 / 旧数据(无闸门) / 观察·<原因>。"""
    if g.get("bettable_ever"):
        return "可下注"
    reasons = g.get("reasons") or Counter()
    if g.get("schema_old") and not reasons:
        return "旧数据(无闸门)"
    if reasons:
        top = reasons.most_common(1)[0][0]
        if "软书" in top:
            book = top.split("聚合源")[-1].split("（")[0].strip()
            return f"观察·软书{book}"
        if "书源数" in top:
            try:
                n = top.split("书源数")[1].split("<")[0].strip()
                return f"观察·仅{n}家书源"
            except IndexError:
                return "观察"
        return "观察"
    if g.get("gated_ever"):
        return "观察·分歧"
    return "观察"


def signal_history(
    conn: sqlite3.Connection, game_id: str, home_score: int | None, away_score: int | None
) -> list[dict]:
    """该场比赛出现过的信号（去重），完赛时附命中/落空结果。"""
    cached = _SIGNAL_CACHE.get(game_id)
    now = time.time()
    if cached and now - cached["at"] < _SIGNAL_CACHE_TTL:
        signals = cached["signals"]
    else:
        rows = conn.execute(
            """
            SELECT created_at, details FROM predictions
            WHERE game_id=? AND details LIKE ? ORDER BY id
            """,
            (game_id, '%"signal"%'),
        ).fetchall()
        signals = merge_signal_versions(
            [(row["created_at"], row["details"]) for row in rows]
        )
        if len(_SIGNAL_CACHE) > 500:  # 日期推进防无界增长
            _SIGNAL_CACHE.clear()
        _SIGNAL_CACHE[game_id] = {"at": now, "signals": signals}
    if home_score is not None and away_score is not None:
        return [
            {**s, "result": settle_edge(s["market"], s["side"], s["line"], home_score, away_score)}
            for s in signals
        ]
    return [dict(s) for s in signals]


def _client() -> httpx.Client:
    return httpx.Client(
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    )


def game_seconds(period: int | None, clock_seconds: float | None) -> float:
    """Seconds of game time played (regular + OT so far)."""
    period = period or 0
    clock = max(clock_seconds or 0.0, 0.0)
    if period <= 4:
        return (period - 1) * PERIOD_SECONDS + (PERIOD_SECONDS - clock)
    return REGULATION_SECONDS + (period - 5) * OT_SECONDS + (OT_SECONDS - clock)


def fraction_played(period: int | None, clock_seconds: float | None) -> float:
    """Fraction of the *pregame expectation* attributable to played time."""
    played = game_seconds(period, clock_seconds)
    return min(max(played / REGULATION_SECONDS, 0.0), 1.0)


def live_projection(
    pregame_margin: float,
    pregame_total: float,
    sigma_margin: float,
    sigma_total: float,
    period: int | None,
    clock_seconds: float | None,
    current_margin: int,
    current_total: int,
) -> dict:
    remaining_factor = 1.0 - fraction_played(period, clock_seconds)
    # sigma uses raw played time so OT keeps shrinking uncertainty
    played = game_seconds(period, clock_seconds)
    sigma_factor = max((REGULATION_SECONDS - played) / REGULATION_SECONDS, 0.05) ** 0.5

    proj_margin = current_margin + pregame_margin * remaining_factor
    proj_total = current_total + pregame_total * remaining_factor
    sigma_m = max(sigma_margin * sigma_factor, 1.0)
    sigma_t = max(sigma_total * sigma_factor, 1.5)

    return {
        "proj_margin": proj_margin,
        "proj_total": proj_total,
        "sigma_margin": sigma_m,
        "sigma_total": sigma_t,
        "home_win_prob": moneyline_prob(proj_margin, sigma_m),
        "remaining_factor": remaining_factor,
    }


def _linescores(competitor: dict) -> list[int | None]:
    """ESPN linescores：每节得分（Q1..Q4/OT）。缺失或异常记 None。"""
    out: list[int | None] = []
    for item in competitor.get("linescores") or []:
        value = item.get("value")
        if value is None:
            value = item.get("displayValue")
        try:
            out.append(int(float(value)) if value not in (None, "") else None)
        except (TypeError, ValueError):
            out.append(None)
    return out


def _parse_event(event: dict) -> dict:
    competition = (event.get("competitions") or [{}])[0]
    competitors = competition.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if home is None or away is None:
        return {}
    status = event.get("status") or {}
    status_type = status.get("type") or {}
    clock = status.get("clock")
    try:
        clock = float(clock) if clock is not None else None
    except (TypeError, ValueError):
        clock = None
    return {
        "game_id": str(event.get("id")),
        "state": status_type.get("state") or "pre",
        "status_detail": status_type.get("shortDetail") or status_type.get("detail"),
        "period": status.get("period"),
        "display_clock": status.get("displayClock"),
        "clock_seconds": clock,
        "home_score": int(home.get("score") or 0),
        "away_score": int(away.get("score") or 0),
        "home_linescores": _linescores(home),
        "away_linescores": _linescores(away),
    }


def _summary_extras(event_id: str) -> dict:
    """ESPN win probability (current + momentum) and the last few plays."""
    try:
        with _client() as client:
            resp = client.get(f"{ESPN_BASE}/summary", params={"event": event_id})
            resp.raise_for_status()
            data = resp.json()
    except Exception:  # noqa: BLE001 - live extras are best-effort
        return {}

    wp_curve = data.get("winprobability") or []
    wp_now = None
    wp_delta = None
    if wp_curve:
        values = [item.get("homeWinPercentage") for item in wp_curve if item.get("homeWinPercentage") is not None]
        if values:
            wp_now = values[-1]
            back = values[max(0, len(values) - 11)]  # ~10 plays ago
            wp_delta = values[-1] - back

    plays = []
    for play in (data.get("plays") or [])[-6:]:
        text = play.get("text")
        plays.append(
            {
                "clock": (play.get("clock") or {}).get("displayValue"),
                "period": (play.get("period") or {}).get("number"),
                "text": text,
                "text_zh": i18n.play_zh(text),
                "home_score": play.get("homeScore"),
                "away_score": play.get("awayScore"),
            }
        )
    return {"espn_home_win_prob": wp_now, "espn_wp_delta": wp_delta, "recent_plays": plays}


def live_market_snapshot(conn: sqlite3.Connection, game_id: str) -> dict | None:
    """该场最近一次滚球盘口快照（解析后的主盘）。

    封盘时（价格全 0 / 市场消失）附上「上一次有效盘口」，
    避免面板直接空白——但仍标记 suspended，不提供下单入口。
    """
    try:
        latest = conn.execute(
            """
            SELECT id, period, clock, score, market_raw, captured_at, suspended
            FROM live_odds WHERE game_id=? ORDER BY id DESC LIMIT 1
            """,
            (game_id,),
        ).fetchone()
    except sqlite3.OperationalError:  # 旧库还没有 live_odds 表
        return None
    if latest is None:
        return None

    from ..ingest.hao import parse_live_market

    def _age(captured_at: str) -> float | None:
        try:
            captured = datetime.fromisoformat(str(captured_at))
            if captured.tzinfo is None:
                captured = captured.replace(tzinfo=timezone.utc)
            return (datetime.now(timezone.utc) - captured).total_seconds()
        except (TypeError, ValueError):
            return None

    def _parse(row) -> dict | None:
        try:
            return parse_live_market(json.loads(row["market_raw"]))
        except (TypeError, ValueError):
            return None

    parsed = _parse(latest)
    if parsed is None:
        return None

    has_prices = bool(
        parsed.get("handicap") or parsed.get("total") or parsed.get("moneyline")
    )
    if not has_prices:
        # 用 suspended 列直接定位最近一次有效快照，避免逐条重新解析
        try:
            prev = conn.execute(
                """
                SELECT period, clock, score, market_raw, captured_at
                FROM live_odds
                WHERE game_id=? AND COALESCE(suspended, 0) = 0 AND id < ?
                ORDER BY id DESC LIMIT 1
                """,
                (game_id, latest["id"]),
            ).fetchone()
        except (sqlite3.OperationalError, sqlite3.DatabaseError):
            prev = None
        if prev is not None:
            prev_parsed = _parse(prev)
            if prev_parsed and (
                prev_parsed.get("handicap")
                or prev_parsed.get("total")
                or prev_parsed.get("moneyline")
            ):
                prev_age = _age(prev["captured_at"])
                parsed["last_valid"] = {
                    **prev_parsed,
                    "period": prev["period"],
                    "clock": prev["clock"],
                    "score": prev["score"],
                    "captured_at": prev["captured_at"],
                    "age_seconds": round(prev_age, 0) if prev_age is not None else None,
                }

    age = _age(latest["captured_at"])
    parsed.update(
        {
            "period": latest["period"],
            "clock": latest["clock"],
            "score": latest["score"],
            "captured_at": latest["captured_at"],
            "age_seconds": round(age, 0) if age is not None else None,
            "stale": (age is not None and age > LIVE_MARKET_STALE_SECONDS),
            "book": "hao-tiyu",
        }
    )
    return parsed


def live_board(conn: sqlite3.Connection, slate_date: str) -> dict:
    """Merge live scoreboard state with pregame predictions."""
    with _client() as client:
        resp = client.get(f"{ESPN_BASE}/scoreboard", params={"dates": slate_date.replace("-", "")})
        resp.raise_for_status()
        payload = resp.json()

    # 纸面注单：按比赛聚合（含已结算，供看板/赛时卡片显示注单与结果）
    bets_by_game: dict[str, list[dict]] = {}
    for row in conn.execute(
        "SELECT game_id, market, side, line, price, stake, result, profit FROM bets ORDER BY id"
    ):
        bets_by_game.setdefault(str(row["game_id"]), []).append(
            {
                "market": row["market"],
                "side": row["side"],
                "line": row["line"],
                "price": row["price"],
                "stake": row["stake"],
                "result": row["result"],
                "profit": row["profit"],
            }
        )

    events = [parsed for parsed in (_parse_event(e) for e in payload.get("events") or []) if parsed]
    games = []
    for event in events:
        game_id = event["game_id"]
        row = conn.execute(
            """
            SELECT g.*, h.abbrev AS home_abbrev, h.name AS home_name,
                   a.abbrev AS away_abbrev, a.name AS away_name
            FROM games g JOIN teams h ON h.id=g.home_id JOIN teams a ON a.id=g.away_id
            WHERE g.id=?
            """,
            (game_id,),
        ).fetchone()
        if row is None:
            continue
        game = dict(row)
        prediction = latest_prediction(conn, game_id)
        pred = prediction["details"].get("model") if prediction else None
        lines = prediction["details"].get("lines") if prediction else {}

        item = {
            "game_id": game_id,
            "matchup": f"{game['away_abbrev']} @ {game['home_abbrev']}",
            "matchup_zh": i18n.matchup_zh(f"{game['away_abbrev']} @ {game['home_abbrev']}"),
            "home_name": game["home_name"],
            "away_name": game["away_name"],
            "home_name_zh": i18n.team_zh(game["home_abbrev"], game["home_name"]),
            "away_name_zh": i18n.team_zh(game["away_abbrev"], game["away_name"]),
            "tipoff": game["tipoff"],
            **event,
            "pregame": pred,
            "lines": lines or {},
            "bets": bets_by_game.get(game_id, []),
        }

        if event["state"] == "in" and pred:
            current_margin = event["home_score"] - event["away_score"]
            current_total = event["home_score"] + event["away_score"]
            proj = live_projection(
                pred["pred_margin"],
                pred["pred_total"],
                pred["sigma_margin"],
                pred["sigma_total"],
                event["period"],
                event["clock_seconds"],
                current_margin,
                current_total,
            )
            item["live"] = proj
            # 校准后的赛中胜率（研究模块）：与 live_projection 的临时口径分开，
            # 用历史逐回合标定的参数，口径可复现、可验收。不进入信号通道。
            item["model_wp"] = round(
                wp_win_prob(
                    current_margin,
                    wp_seconds_remaining(event.get("period"), event.get("clock_seconds")),
                    float(pred["pred_margin"]),
                ),
                4,
            )
            spread_line = lines.get("spread")
            total_line = lines.get("total")
            item["live_cover"] = {
                "spread_home": cover_prob(proj["proj_margin"], spread_line, proj["sigma_margin"])
                if spread_line is not None
                else None,
                "over": over_prob(proj["proj_total"], total_line, proj["sigma_total"])
                if total_line is not None
                else None,
            }
            extras = _summary_extras(game_id)
            item.update(extras)
            # 滚球盘口（好体育）：只展示，不进入信号通道
            item["live_market"] = live_market_snapshot(conn, game_id)
            time.sleep(REQUEST_SLEEP)

        # 滚球盘口（好体育）：进行中的比赛都挂上，仅展示、不进信号通道
        if event["state"] == "in":
            item.setdefault("live_market", live_market_snapshot(conn, game_id))

        # 信号历史（赛后复盘用）：去重合并全部 prediction 版本；完赛附命中结果
        if event["state"] in ("post", "in"):
            item["signals_history"] = signal_history(
                conn, game_id,
                event["home_score"] if event["state"] == "post" else None,
                event["away_score"] if event["state"] == "post" else None,
            )
        games.append(item)

    games.sort(key=lambda g: (g["state"] != "in", g["state"] != "pre", g.get("tipoff") or ""))
    return {
        "date": slate_date,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(games),
        "games": games,
    }


def live_overlay(conn: sqlite3.Connection, slate_date: str) -> dict:
    """本机中转用的「滚球盘口覆盖层」。

    只含进行中比赛的 live_market（约 1-2KB），不含预测/台账。手机静态模式
    拿到后按 game_id 覆盖到 CI 的静态快照上：预测量仍以 github.io 为准，
    只有盘口换成本机 2 分钟级的新数据。

    只读数据库（不调 ESPN）：watch 每轮都会调它，不能为此增加网络请求。
    """
    rows = conn.execute(
        """
        SELECT id FROM games
        WHERE slate_date=? AND status IN ('STATUS_IN_PROGRESS','STATUS_HALFTIME')
        """,
        (slate_date,),
    ).fetchall()
    markets = {}
    for row in rows:
        market = live_market_snapshot(conn, row["id"])
        if market:
            markets[str(row["id"])] = market
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "date": slate_date,
        "count": len(markets),
        "markets": markets,
    }
