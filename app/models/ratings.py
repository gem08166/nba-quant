from __future__ import annotations

import json
import math
import os
import sqlite3
from datetime import date as date_cls
from datetime import datetime, timedelta, timezone

import numpy as np

from ..config import SIGMA_MARGIN_FLOOR, SIGMA_TOTAL_FLOOR
from ..pricing.devig import moneyline_prob

REGULAR_AND_PLAYOFFS = (2, 3)

# Total-points regime recalibration: the weighted ridge (half-life 120d) lags
# mid-season scoring regime shifts, leaving a systematic total bias. Shift the
# total intercept so recent-game predictions match the recent league average.
# The recent average uses exponential weighting (recency half-life below) over
# a lookback window, so the estimate tracks mid-month swings instead of
# averaging them flat like a fixed window does.
TOTAL_REGIME_WINDOW = int(os.getenv("NBAQ_TOTAL_REGIME_WINDOW", "45"))
TOTAL_REGIME_HALFLIFE = float(os.getenv("NBAQ_TOTAL_REGIME_HALFLIFE", "10"))
TOTAL_REGIME_BLEND = float(os.getenv("NBAQ_TOTAL_REGIME_BLEND", "1.0"))
TOTAL_REGIME_MIN_GAMES = int(os.getenv("NBAQ_TOTAL_REGIME_MIN_GAMES", "10"))
# Skip recalibration across an offseason: last season's scoring regime says
# nothing about opening night, and applying it actively hurts.
TOTAL_REGIME_MAX_GAP = int(os.getenv("NBAQ_TOTAL_REGIME_MAX_GAP", "10"))


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _d(value: str) -> date_cls:
    return date_cls.fromisoformat(value[:10])


def load_finals(
    conn: sqlite3.Connection,
    before: str | None = None,
    season_types: tuple[int, ...] = REGULAR_AND_PLAYOFFS,
) -> list[dict]:
    sql = """
        SELECT * FROM games
        WHERE home_score IS NOT NULL AND away_score IS NOT NULL
          -- Real NBA teams have small ESPN ids (1-30); special-event teams
          -- (All-Star Rising Stars "Team Stars/Stripes/World", exhibitions
          -- vs e.g. London Lions) get large ids and would poison training.
          AND home_id BETWEEN 1 AND 30 AND away_id BETWEEN 1 AND 30
    """
    params: list[object] = []
    if before:
        sql += " AND slate_date < ?"
        params.append(before)
    if season_types:
        placeholders = ",".join("?" for _ in season_types)
        sql += f" AND (season_type IS NULL OR season_type IN ({placeholders}))"
        params.extend(season_types)
    sql += " ORDER BY slate_date, COALESCE(tipoff, '')"
    return [dict(row) for row in conn.execute(sql, params)]


def compute_elo(games: list[dict], k: float = 20.0, home_advantage: float = 100.0) -> tuple[dict, dict]:
    """Return (pregame ratings per game, final ratings). No look-ahead."""
    ratings: dict[int, float] = {}
    pregame: dict[str, tuple[float, float]] = {}
    ordered = sorted(games, key=lambda g: (g["slate_date"], g.get("tipoff") or ""))

    for game in ordered:
        home, away = int(game["home_id"]), int(game["away_id"])
        elo_home = ratings.get(home, 1500.0)
        elo_away = ratings.get(away, 1500.0)
        pregame[game["id"]] = (elo_home, elo_away)

        expected = 1.0 / (1.0 + 10.0 ** (-((elo_home - elo_away + home_advantage) / 400.0)))
        if game["home_score"] > game["away_score"]:
            actual = 1.0
        elif game["home_score"] < game["away_score"]:
            actual = 0.0
        else:
            actual = 0.5
        margin = abs(int(game["home_score"]) - int(game["away_score"]))
        multiplier = ((margin + 3.0) ** 0.8) / (7.5 + 0.006 * abs(elo_home - elo_away))
        delta = k * multiplier * (actual - expected)
        ratings[home] = elo_home + delta
        ratings[away] = elo_away - delta

    return pregame, ratings


def compute_rest(games: list[dict]) -> dict[str, tuple[int, int]]:
    last: dict[int, date_cls] = {}
    result: dict[str, tuple[int, int]] = {}
    ordered = sorted(games, key=lambda g: (g["slate_date"], g.get("tipoff") or ""))
    for game in ordered:
        day = _d(game["slate_date"])
        home, away = int(game["home_id"]), int(game["away_id"])
        rest_home = (day - last[home]).days if home in last else 7
        rest_away = (day - last[away]).days if away in last else 7
        result[game["id"]] = (min(max(rest_home, 0), 7), min(max(rest_away, 0), 7))
        last[home] = day
        last[away] = day
    return result


def _ridge_solve(X: list[list[float]], y: list[float], weights: list[float], penalty: list[float]) -> np.ndarray:
    Xa = np.asarray(X, dtype=float)
    ya = np.asarray(y, dtype=float)
    wa = np.sqrt(np.asarray(weights, dtype=float))
    Xw = Xa * wa[:, None]
    yw = ya * wa
    A = Xw.T @ Xw + np.diag(np.asarray(penalty, dtype=float))
    b = Xw.T @ yw
    return np.linalg.solve(A, b)


def fit(
    conn: sqlite3.Connection,
    as_of: str | None = None,
    lam: float = 8.0,
    half_life_days: float = 120.0,
) -> dict:
    """Fit a Massey-style margin model and a scoring-environment total model."""
    games = load_finals(conn, before=as_of)
    if len(games) < 30:
        raise ValueError(f"not enough completed games to fit: {len(games)}")

    pregame_elo, _ = compute_elo(games)
    rest = compute_rest(games)
    teams = sorted({int(g["home_id"]) for g in games} | {int(g["away_id"]) for g in games})
    index = {team: i for i, team in enumerate(teams)}
    n_teams = len(teams)

    X_margin: list[list[float]] = []
    y_margin: list[float] = []
    X_total: list[list[float]] = []
    y_total: list[float] = []
    weights: list[float] = []

    max_day = max(_d(g["slate_date"]) for g in games)
    for game in games:
        home, away = int(game["home_id"]), int(game["away_id"])
        elo_home, elo_away = pregame_elo[game["id"]]
        rest_home, rest_away = rest[game["id"]]
        age = (max_day - _d(game["slate_date"])).days
        weight = 0.5 ** (age / half_life_days)

        row_m = [1.0, elo_home - elo_away, float(rest_home - rest_away)] + [0.0] * n_teams
        row_m[3 + index[home]] = 1.0
        row_m[3 + index[away]] = -1.0
        X_margin.append(row_m)
        y_margin.append(float(game["home_score"] - game["away_score"]))

        row_t = [1.0] + [0.0] * n_teams
        row_t[1 + index[home]] = 1.0
        row_t[1 + index[away]] = 1.0
        X_total.append(row_t)
        y_total.append(float(game["home_score"] + game["away_score"]))
        weights.append(weight)

    beta_m = _ridge_solve(X_margin, y_margin, weights, [0.0, 0.0, 0.0] + [lam] * n_teams)
    beta_t = _ridge_solve(X_total, y_total, weights, [0.0] + [lam] * n_teams)

    # Recalibrate the total intercept toward the recent league scoring level.
    # Regular-season games only: playoff scoring environments differ and would
    # poison the estimate at the start of a new season.
    regime_shift = 0.0
    if TOTAL_REGIME_BLEND > 0.0:
        as_of_day = _d(as_of) if as_of else max_day + timedelta(days=1)
        last_game_day = max(_d(g["slate_date"]) for g in games)
        gap_days = (as_of_day - last_game_day).days
        window_start = max_day - timedelta(days=TOTAL_REGIME_WINDOW)
        recent = [
            g
            for g in games
            if _d(g["slate_date"]) >= window_start
            and (g.get("season_type") is None or g.get("season_type") == 2)
        ]
        if 0 <= gap_days <= TOTAL_REGIME_MAX_GAP and len(recent) >= TOTAL_REGIME_MIN_GAMES:
            # Exponential recency weights: a flat window averages the month
            # flat and lags mid-month swings; half-life ~10 days tracks them.
            rw = np.array(
                [
                    0.5 ** ((max_day - _d(g["slate_date"])).days / TOTAL_REGIME_HALFLIFE)
                    for g in recent
                ]
            )
            actual_arr = np.array([float(g["home_score"]) + float(g["away_score"]) for g in recent])
            pred_arr = np.array(
                [
                    beta_t[0] + beta_t[1 + index[int(g["home_id"])]]
                    + beta_t[1 + index[int(g["away_id"])]]
                    for g in recent
                ]
            )
            actual_avg = float(np.sum(rw * actual_arr) / np.sum(rw))
            pred_avg = float(np.sum(rw * pred_arr) / np.sum(rw))
            regime_shift = float(actual_avg - pred_avg)
            beta_t[0] += TOTAL_REGIME_BLEND * regime_shift

    res_m = np.asarray(y_margin) - np.asarray(X_margin) @ beta_m
    res_t = np.asarray(y_total) - np.asarray(X_total) @ beta_t
    w = np.asarray(weights)
    sigma_m = math.sqrt(float(np.sum(w * res_m * res_m) / np.sum(w)))
    sigma_t = math.sqrt(float(np.sum(w * res_t * res_t) / np.sum(w)))

    team_params = {
        str(team): {"net": float(beta_m[3 + i]), "env": float(beta_t[1 + i])}
        for team, i in index.items()
    }
    return {
        "created_at": _now(),
        "as_of": as_of,
        "n_games": len(games),
        "teams": team_params,
        "margin_intercept": float(beta_m[0]),
        "margin_elo": float(beta_m[1]),
        "margin_rest": float(beta_m[2]),
        "total_intercept": float(beta_t[0]),
        "total_regime_shift": regime_shift,
        "sigma_margin": max(sigma_m, SIGMA_MARGIN_FLOOR),
        "sigma_total": max(sigma_t, SIGMA_TOTAL_FLOOR),
        "raw_sigma_margin": sigma_m,
        "raw_sigma_total": sigma_t,
    }


def predict(
    model: dict,
    home_id: int,
    away_id: int,
    elo_home: float,
    elo_away: float,
    rest_home: int,
    rest_away: int,
    injury_home: float = 0.0,
    injury_away: float = 0.0,
) -> dict:
    home_params = model["teams"].get(str(home_id), {"net": 0.0, "env": 0.0})
    away_params = model["teams"].get(str(away_id), {"net": 0.0, "env": 0.0})

    margin = (
        model["margin_intercept"]
        + model["margin_elo"] * (elo_home - elo_away)
        + model["margin_rest"] * (rest_home - rest_away)
        + home_params["net"]
        - away_params["net"]
        - injury_home
        + injury_away
    )
    total = (
        model["total_intercept"]
        + home_params["env"]
        + away_params["env"]
        - 0.5 * (injury_home + injury_away)
    )
    sigma_m = model["sigma_margin"]
    sigma_t = model["sigma_total"]
    return {
        "pred_margin": margin,
        "sigma_margin": sigma_m,
        "pred_total": total,
        "sigma_total": sigma_t,
        "home_win_prob": moneyline_prob(margin, sigma_m),
    }


def save_model(conn: sqlite3.Connection, model: dict) -> int:
    cur = conn.execute(
        """
        INSERT INTO models(created_at, n_games, sigma_margin, sigma_total, params)
        VALUES(?, ?, ?, ?, ?)
        """,
        (
            model["created_at"],
            model["n_games"],
            model["sigma_margin"],
            model["sigma_total"],
            json.dumps(model, ensure_ascii=False),
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def latest_model(conn: sqlite3.Connection) -> dict | None:
    row = conn.execute("SELECT params FROM models ORDER BY id DESC LIMIT 1").fetchone()
    return json.loads(row["params"]) if row else None


def train_and_save(conn: sqlite3.Connection, as_of: str | None = None) -> dict:
    model = fit(conn, as_of=as_of)
    save_model(conn, model)
    return model


def elo_as_of(conn: sqlite3.Connection, slate_date: str) -> dict[int, float]:
    games = load_finals(conn, before=slate_date)
    if not games:
        return {}
    _, final = compute_elo(games)
    return final


def rest_as_of(conn: sqlite3.Connection, slate_date: str, team_ids: list[int]) -> dict[int, int]:
    day = _d(slate_date)
    games = load_finals(conn, before=slate_date)
    last: dict[int, date_cls] = {}
    for game in games:
        game_day = _d(game["slate_date"])
        for team in (int(game["home_id"]), int(game["away_id"])):
            if team not in last or game_day > last[team]:
                last[team] = game_day
    return {
        team: min(max((day - last[team]).days, 0), 7) if team in last else 7
        for team in team_ids
    }
