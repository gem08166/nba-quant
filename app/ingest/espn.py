from __future__ import annotations

import json
import time
from typing import Any

import httpx

from ..config import ESPN_BASE, RAW_DIR, REQUEST_SLEEP, REQUEST_TIMEOUT, USER_AGENT


class ESPNError(RuntimeError):
    pass


def _client() -> httpx.Client:
    return httpx.Client(
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
            "Accept-Language": "en-US,en;q=0.9",
        },
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
    )


def get_json(path: str, params: dict[str, Any] | None = None, retries: int = 3) -> dict:
    last: Exception | None = None
    with _client() as client:
        for attempt in range(retries):
            try:
                resp = client.get(f"{ESPN_BASE}{path}", params=params)
                resp.raise_for_status()
                return resp.json()
            except Exception as exc:  # noqa: BLE001 - network errors vary
                last = exc
                time.sleep(0.6 * (attempt + 1))
    raise ESPNError(f"ESPN request failed: {last}")


def fetch_scoreboard(slate_date: str | None = None) -> dict:
    # ESPN expects YYYYMMDD, while the rest of the app stores ISO dates.
    params = {"dates": slate_date.replace("-", "")} if slate_date else None
    return get_json("/scoreboard", params=params)


def fetch_summary(event_id: str) -> dict:
    return get_json("/summary", params={"event": event_id})


def save_raw(slate_date: str, name: str, payload: dict) -> None:
    folder = RAW_DIR / slate_date
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _num(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip().replace("−", "-")
    if not text:
        return None
    text = text.lstrip("oOuU")
    try:
        return float(text)
    except ValueError:
        return None


def american_to_decimal(value: Any) -> float | None:
    if value in (None, ""):
        return None
    try:
        american = float(str(value).strip().replace("+", "").replace("−", "-"))
    except ValueError:
        return None
    if american == 0:
        return None
    if american > 0:
        return 1.0 + american / 100.0
    return 1.0 + 100.0 / abs(american)


def _team(team: dict) -> dict:
    return {
        "id": int(team["id"]),
        "abbrev": team.get("abbreviation") or team.get("shortDisplayName") or str(team["id"]),
        "name": team.get("displayName") or team.get("name") or "",
        "logo": (team.get("logo") or ""),
    }


def _close(node: dict, key: str = "close") -> dict:
    return node.get(key) or {}


def parse_odds(competition: dict) -> list[dict]:
    rows: list[dict] = []
    for odds in competition.get("odds") or []:
        book = ((odds.get("provider") or {}).get("name") or "unknown").strip()
        spread_block = odds.get("pointSpread") or {}
        home_close = _close(spread_block.get("home") or {})
        away_close = _close(spread_block.get("away") or {})

        home_line = _num(home_close.get("line"))
        if home_line is None:
            home_line = _num(odds.get("spread"))
        away_line = _num(away_close.get("line"))
        if away_line is None and home_line is not None:
            away_line = -home_line

        home_price = american_to_decimal(home_close.get("odds"))
        away_price = american_to_decimal(away_close.get("odds"))

        if home_line is not None:
            rows.append({"book": book, "market": "spread", "side": "home", "line": home_line, "price": home_price})
        if away_line is not None:
            rows.append({"book": book, "market": "spread", "side": "away", "line": away_line, "price": away_price})

        total_block = odds.get("total") or {}
        over_close = _close(total_block.get("over") or {})
        under_close = _close(total_block.get("under") or {})
        total_line = _num(odds.get("overUnder"))
        if total_line is None:
            total_line = _num(over_close.get("line")) or _num(under_close.get("line"))
        over_price = american_to_decimal(over_close.get("odds"))
        under_price = american_to_decimal(under_close.get("odds"))
        if total_line is not None:
            rows.append({"book": book, "market": "total", "side": "over", "line": total_line, "price": over_price})
            rows.append({"book": book, "market": "total", "side": "under", "line": total_line, "price": under_price})

        ml_block = odds.get("moneyline") or {}
        ml_home = american_to_decimal(_close(ml_block.get("home") or {}).get("odds"))
        ml_away = american_to_decimal(_close(ml_block.get("away") or {}).get("odds"))
        if ml_home is not None:
            rows.append({"book": book, "market": "moneyline", "side": "home", "line": None, "price": ml_home})
        if ml_away is not None:
            rows.append({"book": book, "market": "moneyline", "side": "away", "line": None, "price": ml_away})
    return rows


def parse_event(event: dict, slate_date: str) -> dict:
    competition = (event.get("competitions") or [{}])[0]
    competitors = competition.get("competitors") or []
    home = next((c for c in competitors if c.get("homeAway") == "home"), None)
    away = next((c for c in competitors if c.get("homeAway") == "away"), None)
    if home is None or away is None:
        raise ESPNError(f"event {event.get('id')} has no home/away competitors")

    status = (event.get("status") or {}).get("type") or {}
    completed = bool(status.get("completed"))

    def score(node: dict) -> int | None:
        value = node.get("score")
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    home_score_val = score(home)
    away_score_val = score(away)

    season = event.get("season") or {}
    season_type = season.get("type")
    year = season.get("year")
    if year:
        season_label = f"{int(year) - 1}-{str(int(year))[-2:]}"
    else:
        y = int(slate_date[:4])
        month = int(slate_date[5:7])
        season_label = f"{y}-{str(y + 1)[-2:]}" if month >= 9 else f"{y - 1}-{str(y)[-2:]}"

    return {
        "id": str(event["id"]),
        "season": season_label,
        "season_type": int(season_type) if season_type is not None else None,
        "slate_date": slate_date,
        "tipoff": event.get("date"),
        "home": _team(home["team"]),
        "away": _team(away["team"]),
        # home_score/away_score 语义 = 完赛比分（训练/回测用 score IS NOT NULL
        # 判断「有结果」，绝不写进行中的比分）；进行中的比分放 live_* 独立列
        "home_score": home_score_val if completed else None,
        "away_score": away_score_val if completed else None,
        "live_home_score": None if completed else home_score_val,
        "live_away_score": None if completed else away_score_val,
        "status": status.get("name") or "STATUS_UNKNOWN",
        "status_detail": status.get("detail"),
        "venue": (competition.get("venue") or {}).get("fullName"),
        "odds": parse_odds(competition),
    }


def parse_injuries(summary: dict) -> list[dict]:
    rows: list[dict] = []
    for block in summary.get("injuries") or []:
        team = block.get("team") or {}
        try:
            team_id = int(team.get("id"))
        except (TypeError, ValueError):
            continue
        for injury in block.get("injuries") or []:
            athlete = injury.get("athlete") or {}
            player = athlete.get("displayName") or athlete.get("fullName") or ""
            if not player:
                continue
            status = (injury.get("type") or {}).get("description") or injury.get("status") or ""
            details = injury.get("details") or {}
            detail_parts = [details.get("type"), details.get("detail"), details.get("side")]
            detail = " · ".join(str(p) for p in detail_parts if p)
            rows.append(
                {
                    "team_id": team_id,
                    "player": player,
                    "status": status,
                    "detail": detail,
                }
            )
    return rows


def ingest_summary_injuries(event_id: str, slate_date: str) -> list[dict]:
    summary = fetch_summary(event_id)
    save_raw(slate_date, f"summary_{event_id}", summary)
    time.sleep(REQUEST_SLEEP)
    return parse_injuries(summary)


# --- Play-by-play / lineups (RAPM source data) ------------------------------

def parse_players(summary: dict) -> list[dict]:
    """Player directory (ESPN athlete ids -> names + current team)."""
    seen: set[int] = set()
    rows: list[dict] = []
    for block in (summary.get("boxscore") or {}).get("players") or []:
        team_id = int((block.get("team") or {}).get("id"))
        for stat_group in block.get("statistics") or []:
            for athlete in stat_group.get("athletes") or []:
                node = athlete.get("athlete") or {}
                try:
                    player_id = int(node.get("id"))
                except (TypeError, ValueError):
                    continue
                if player_id in seen:
                    continue
                seen.add(player_id)
                rows.append(
                    {
                        "id": player_id,
                        "name": node.get("displayName") or node.get("fullName") or str(player_id),
                        "team_id": team_id,
                    }
                )
    return rows


def parse_starters(summary: dict, home_id: int, away_id: int) -> dict[int, list[int]]:
    """Starting lineups from boxscore `starter` flags. {team_id: [player_id]*5}."""
    result: dict[int, list[int]] = {}
    mapping = {}
    for block in (summary.get("boxscore") or {}).get("players") or []:
        team_id = int((block.get("team") or {}).get("id"))
        mapping[team_id] = block
    for team_id in (home_id, away_id):
        block = mapping.get(team_id)
        if not block:
            continue
        starters: list[int] = []
        for stat_group in block.get("statistics") or []:
            for athlete in stat_group.get("athletes") or []:
                if athlete.get("starter"):
                    try:
                        starters.append(int(athlete["athlete"]["id"]))
                    except (KeyError, TypeError, ValueError):
                        continue
        if starters:
            result[team_id] = starters
    return result


def parse_plays(summary: dict) -> list[dict]:
    """Normalized play rows. Substitution rows carry player_in / player_out."""
    rows: list[dict] = []
    for play in summary.get("plays") or []:
        try:
            seq = int(play["sequenceNumber"])
        except (KeyError, TypeError, ValueError):
            continue
        team_id = None
        try:
            team_id = int(play["team"]["id"])
        except (KeyError, TypeError, ValueError):
            pass
        participants = play.get("participants") or []
        player_in = player_out = None
        if (play.get("type") or {}).get("text") == "Substitution" and len(participants) >= 2:
            # ESPN convention: participants[0] enters, participants[1] exits.
            try:
                player_in = int(participants[0]["athlete"]["id"])
                player_out = int(participants[1]["athlete"]["id"])
            except (KeyError, TypeError, ValueError):
                pass
        rows.append(
            {
                "seq": seq,
                "period": (play.get("period") or {}).get("number"),
                "clock": (play.get("clock") or {}).get("displayValue"),
                "play_type": (play.get("type") or {}).get("text"),
                "team_id": team_id,
                "player_in": player_in,
                "player_out": player_out,
                "home_score": play.get("homeScore"),
                "away_score": play.get("awayScore"),
            }
        )
    rows.sort(key=lambda r: r["seq"])
    return rows
