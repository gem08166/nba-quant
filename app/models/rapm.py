from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone

import numpy as np

from .ratings import _d, _ridge_solve

# Free-data constraints shape this model:
# - ESPN play-by-play gives substitution events + running score, enough to
#   reconstruct on-court lineups without cdn.nba.com (which is 403).
# - A "stint" is the span between on-court changes; y is the home margin over
#   the stint, normalized per 100 possessions (estimated from play types).
#
# The regression is single-number RAPM: one coefficient per player, +1 for the
# 5 home players, -1 for the 5 away players, plus a home-court intercept.
# Rating is roughly "net points per 100 possessions while on the floor".

FT_COEF = 0.44


def _possessions(plays: list[dict]) -> int:
    """Estimate possessions in a span of plays: FGA + 0.44*FTA - OREB + TOV."""
    fga = fta = oreb = tov = 0
    for play in plays:
        text = play["play_type"] or ""
        if "Free Throw" in text:
            fta += 1
        elif "Rebound" in text:
            if text.startswith("Offensive"):
                oreb += 1
        elif "Turnover" in text:
            tov += 1
        elif "Jumpball" in text:
            pass
        elif any(tag in text for tag in ("Shot", "Layup", "Dunk", "Jump", "Hook", "Tip Shot", "Fade")):
            fga += 1
    return max(int(round(fga + FT_COEF * fta - oreb + tov)), 1)


def build_stints(
    starters: dict[int, list[int]],
    plays: list[dict],
    game_id: str,
    home_id: int,
    away_id: int,
) -> list[dict]:
    """Reconstruct on-court stints from substitution events.

    Returns one row per stint with both lineups, the home margin accumulated
    over the stint, and an estimated possession count. Tolerant to the usual
    ESPN PBP quirks (missing sub events, odd participant ordering).
    """
    home_court = list(starters.get(home_id) or [])
    away_court = list(starters.get(away_id) or [])
    if len(home_court) != 5 or len(away_court) != 5:
        return []  # cannot seed lineups reliably; skip game

    stints: list[dict] = []
    span: list[dict] = []
    diff_start: float | None = None
    last_diff: float | None = None

    def close_stint() -> None:
        nonlocal span, diff_start, last_diff
        if not span or diff_start is None or last_diff is None:
            span, diff_start = [], None
            return
        margin = last_diff - diff_start
        poss = _possessions(span)
        if poss >= 1 and len(home_court) == 5 and len(away_court) == 5:
            stints.append(
                {
                    "game_id": game_id,
                    "home_players": list(home_court),
                    "away_players": list(away_court),
                    "margin": margin,
                    "possessions": poss,
                }
            )
        span, diff_start = [], None

    for play in plays:
        diff = None
        home_score, away_score = play.get("home_score"), play.get("away_score")
        if home_score is not None and away_score is not None:
            diff = float(home_score) - float(away_score)
            if diff_start is None:
                diff_start = diff
            last_diff = diff

        if play["play_type"] == "Substitution" and play["team_id"] in (home_id, away_id):
            close_stint()
            # The boundary diff (this sub's running score) seeds the next
            # stint: the first scoring play *inside* the next stint carries
            # its own points, so using it as the baseline would drop them.
            if diff is not None:
                diff_start = diff
                last_diff = diff
            player_in, player_out = play.get("player_in"), play.get("player_out")
            court = home_court if play["team_id"] == home_id else away_court
            if player_out in court:
                court.remove(player_out)
            if player_in is not None and player_in not in court:
                court.append(player_in)
            # Tolerant repair: a missed earlier sub can push a court over 5.
            while len(court) > 5:
                court.pop(0)
            continue

        span.append(play)

    close_stint()
    return stints


def load_all_pbp(conn: sqlite3.Connection) -> dict[str, tuple[dict, dict[int, list[int]], list[dict]]]:
    """{game_id: (game_row, starters, plays)} for every game that has PBP."""
    games = {
        row["id"]: dict(row)
        for row in conn.execute(
            "SELECT * FROM games WHERE home_id IS NOT NULL AND away_id IS NOT NULL"
            " AND home_id BETWEEN 1 AND 30 AND away_id BETWEEN 1 AND 30"
        )
    }
    result = {}
    game_ids = [row["game_id"] for row in conn.execute("SELECT DISTINCT game_id FROM pbp_plays")]
    for game_id in game_ids:
        game = games.get(game_id)
        if game is None:
            continue
        starters_rows = conn.execute(
            "SELECT team_id, player_id FROM starters WHERE game_id=?", (game_id,)
        ).fetchall()
        starters: dict[int, list[int]] = {}
        for row in starters_rows:
            starters.setdefault(int(row["team_id"]), []).append(int(row["player_id"]))
        plays = [
            dict(row)
            for row in conn.execute("SELECT * FROM pbp_plays WHERE game_id=? ORDER BY seq", (game_id,))
        ]
        result[game_id] = (game, starters, plays)
    return result


def fit(
    conn: sqlite3.Connection,
    as_of: str | None = None,
    lam: float = 300.0,
    half_life_days: float = 120.0,
    min_possessions: int = 400,
) -> dict:
    """Fit single-number RAPM over all games with PBP (before as_of)."""
    pbp = load_all_pbp(conn)
    if not pbp:
        raise ValueError("no PBP data; run scripts/backfill_pbp.py first")

    all_stints: list[dict] = []
    skipped_quality = 0
    for game_id, (game, starters, plays) in pbp.items():
        if as_of and game["slate_date"] >= as_of:
            continue
        game_stints = build_stints(
            starters, plays, game_id, int(game["home_id"]), int(game["away_id"])
        )
        # Data-quality gate: reconstructed stint margins must reconcile with
        # the final score, otherwise the game's PBP is broken (missing sub or
        # score events) and would inject phantom points.
        if game["home_score"] is not None and game["away_score"] is not None:
            final_margin = int(game["home_score"]) - int(game["away_score"])
            stint_margin = sum(s["margin"] for s in game_stints)
            if abs(stint_margin - final_margin) > 6:
                skipped_quality += 1
                continue
        all_stints.extend(game_stints)
    if not all_stints:
        raise ValueError("no valid stints reconstructed from PBP")

    # Recency weighting at the game level (same convention as ratings.fit).
    max_day = max(_d(game["slate_date"]) for game, _, _ in pbp.values())
    weight_by_game = {
        game_id: 0.5 ** ((max_day - _d(game["slate_date"])).days / half_life_days)
        for game_id, (game, _, _) in pbp.items()
    }

    players = sorted({p for s in all_stints for p in s["home_players"] + s["away_players"]})
    index = {player: i for i, player in enumerate(players)}
    n_players = len(players)

    X: list[list[float]] = []
    y: list[float] = []
    w: list[float] = []
    for stint in all_stints:
        poss = stint["possessions"]
        row = [1.0] + [0.0] * n_players
        for p in stint["home_players"]:
            row[1 + index[p]] = 1.0
        for p in stint["away_players"]:
            row[1 + index[p]] = -1.0
        X.append(row)
        y.append(100.0 * stint["margin"] / poss)
        w.append(weight_by_game[stint["game_id"]] * poss)

    beta = _ridge_solve(X, y, w, [0.0] + [lam] * n_players)

    ya = np.asarray(y)
    wa = np.sqrt(np.asarray(w))
    res = ya - np.asarray(X) @ beta
    sigma = math.sqrt(float(np.sum(wa * res * res) / np.sum(wa)))

    total_poss = sum(s["possessions"] for s in all_stints)
    ratings_out = {str(p): float(beta[1 + i]) for p, i in index.items()}
    names = {
        str(row["id"]): row["name"]
        for row in conn.execute("SELECT id, name FROM players")
    }
    ranked = sorted(ratings_out.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "as_of": as_of,
        "n_games": len(pbp),
        "n_stints": len(all_stints),
        "skipped_quality": skipped_quality,
        "possessions": total_poss,
        "avg_poss_per_stint": round(total_poss / len(all_stints), 2),
        "sigma": sigma,
        "lam": lam,
        "ratings": ratings_out,
        "names": names,
        "top": [{"id": pid, "name": names.get(pid, pid), "rating": round(v, 2)} for pid, v in ranked[:15]],
        "bottom": [{"id": pid, "name": names.get(pid, pid), "rating": round(v, 2)} for pid, v in ranked[-15:]],
    }


def save_model(conn: sqlite3.Connection, model: dict) -> int:
    cur = conn.execute(
        "INSERT INTO rapm_models(created_at, n_games, n_stints, params) VALUES(?, ?, ?, ?)",
        (model["created_at"], model["n_games"], model["n_stints"], json.dumps(model, ensure_ascii=False)),
    )
    conn.commit()
    return int(cur.lastrowid)


def latest_model(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT params FROM rapm_models ORDER BY id DESC LIMIT 1").fetchone()
    return json.loads(row["params"]) if row else None


def player_rating(model: dict, player_id: int) -> float | None:
    value = model["ratings"].get(str(player_id))
    return float(value) if value is not None else None


# Absence costs less than the full on-court rating: starters play ~75-85% of
# possessions, and lineups adapt. Out/Doubtful -> 0.8, Questionable/DTD -> 0.4.
_STATUS_FACTOR = {
    "out": 0.8,
    "doubtful": 0.8,
    "questionable": 0.4,
    "day-to-day": 0.4,
}


def suggest_impacts(conn: sqlite3.Connection, game_id: str, model: dict | None = None) -> list[dict]:
    """RAPM-based impact suggestions for a game's injured players.

    Matches injuries (stored by display name) to PBP player ids via the
    players table. Only suggests; the manual `impact` field stays authoritative.
    """
    model = model or latest_model(conn)
    if not model:
        return []
    id_by_name = {}
    for player_id, name in (model.get("names") or {}).items():
        id_by_name[name.casefold()] = player_id

    suggestions = []
    rows = conn.execute(
        "SELECT team_id, player, status, impact FROM injuries WHERE game_id=?",
        (game_id,),
    ).fetchall()
    for row in rows:
        factor = _STATUS_FACTOR.get((row["status"] or "").casefold())
        if factor is None:
            continue
        player_id = id_by_name.get((row["player"] or "").casefold())
        if player_id is None:
            continue
        rating = player_rating(model, int(player_id))
        if rating is None:
            continue
        suggestions.append(
            {
                "team_id": int(row["team_id"]),
                "player": row["player"],
                "status": row["status"],
                "rapm_rating": round(rating, 2),
                "suggested_impact": round(max(0.0, rating * factor), 1),
                "manual_impact": float(row["impact"] or 0.0),
            }
        )
    return suggestions
