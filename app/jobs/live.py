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

import sqlite3
import time
from datetime import datetime, timezone

import httpx

from ..config import ESPN_BASE, REQUEST_SLEEP, REQUEST_TIMEOUT, USER_AGENT
from ..models import ratings
from ..pricing.devig import cover_prob, moneyline_prob, over_prob
from .predict import latest_prediction
from .. import i18n

PERIOD_SECONDS = 720
OT_SECONDS = 300
REGULATION_SECONDS = 4 * PERIOD_SECONDS
LIVE_STATES = {"in"}


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
            time.sleep(REQUEST_SLEEP)
        games.append(item)

    games.sort(key=lambda g: (g["state"] != "in", g["state"] != "pre", g.get("tipoff") or ""))
    return {
        "date": slate_date,
        "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(games),
        "games": games,
    }
