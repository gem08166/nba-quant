from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone

from .. import db
from ..config import EDGE_THRESHOLD, KELLY_FRACTION, MARKET_BLEND, MAX_STAKE_PCT, MIN_EV
from ..models import rapm, ratings
from ..pricing.devig import (
    cover_prob,
    edge as edge_value,
    expected_value,
    fair_two_way,
    kelly_fraction,
    moneyline_prob,
    over_prob,
)

BOOK_PRIORITY = ("Draft Kings", "DraftKings", "ESPN BET", "BetMGM", "Caesars")

# Never predict games that will never be played (postponed/canceled): they
# would otherwise hang on the board as "未开赛" forever and accumulate
# meaningless prediction rows.
SKIP_STATUSES = ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def latest_odds(conn: sqlite3.Connection, game_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT * FROM odds_snapshots WHERE game_id=? ORDER BY id",
        (game_id,),
    ).fetchall()
    latest: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        latest[(row["book"], row["market"], row["side"])] = dict(row)
    return list(latest.values())


def pick_book(rows: list[dict]) -> str | None:
    books = {row["book"] for row in rows}
    for book in BOOK_PRIORITY:
        if book in books:
            return book
    return sorted(books)[0] if books else None


def _side(by_key: dict[tuple[str, str], dict], market: str, side: str) -> dict | None:
    return by_key.get((market, side))


def _make_edge(
    market: str,
    side: str,
    line: float | None,
    price: float | None,
    model_prob: float,
    fair_prob: float,
    book: str,
) -> dict | None:
    if price is None or price <= 1.0:
        return None
    ev = expected_value(model_prob, price)
    edge = edge_value(model_prob, fair_prob)
    kelly = kelly_fraction(model_prob, price)
    return {
        "market": market,
        "side": side,
        "line": line,
        "price": price,
        "book": book,
        "model_prob": model_prob,
        "fair_prob": fair_prob,
        "edge": edge,
        "ev": ev,
        "kelly": kelly,
        "stake_pct": min(kelly * KELLY_FRACTION, MAX_STAKE_PCT),
        "signal": bool(edge >= EDGE_THRESHOLD and ev >= MIN_EV and kelly > 0),
    }


def build_market(
    pred: dict,
    odds_rows: list[dict],
) -> tuple[str | None, dict]:
    book = pick_book(odds_rows)
    by_key = {(row["market"], row["side"]): row for row in odds_rows if row["book"] == book}
    model_mu_m = pred["pred_margin"]
    model_mu_t = pred["pred_total"]
    sigma_m = pred["sigma_margin"]
    sigma_t = pred["sigma_total"]

    home_spread = _side(by_key, "spread", "home")
    away_spread = _side(by_key, "spread", "away")
    spread_line = home_spread["line"] if home_spread and home_spread["line"] is not None else None
    if spread_line is None and away_spread and away_spread["line"] is not None:
        spread_line = -away_spread["line"]

    total_row = _side(by_key, "total", "over") or _side(by_key, "total", "under")
    total_line = total_row["line"] if total_row else None

    home_ml = _side(by_key, "moneyline", "home")
    away_ml = _side(by_key, "moneyline", "away")

    # Blend the model with the market line. Free sources give only one soft
    # book, and early-season team ratings are stale, so a pure model would
    # generate far too many fake edges.
    pricing_mu_m = model_mu_m
    pricing_mu_t = model_mu_t
    market_mu_m = -spread_line if spread_line is not None else None
    if market_mu_m is not None:
        pricing_mu_m = MARKET_BLEND * model_mu_m + (1.0 - MARKET_BLEND) * market_mu_m
    if total_line is not None:
        pricing_mu_t = MARKET_BLEND * model_mu_t + (1.0 - MARKET_BLEND) * total_line

    probabilities: dict[str, float] = {}
    edges: list[dict] = []

    if spread_line is not None:
        p_home = cover_prob(pricing_mu_m, spread_line, sigma_m)
        p_away = 1.0 - p_home
        probabilities["spread_home"] = p_home
        probabilities["spread_away"] = p_away
        home_price = home_spread["price"] if home_spread else None
        away_price = away_spread["price"] if away_spread else None
        if home_price is None and away_spread:
            home_price = away_spread["price"]
        if away_price is None and home_spread:
            away_price = home_spread["price"]
        fair_home, fair_away = fair_two_way(home_price, away_price)
        for side, p, fair, row, side_line in (
            ("home", p_home, fair_home, home_spread, spread_line),
            ("away", p_away, fair_away, away_spread, -spread_line),
        ):
            if row is None:
                continue
            signal = _make_edge("spread", side, side_line, row.get("price"), p, fair, book or "")
            if signal:
                edges.append(signal)

    if total_line is not None:
        p_over = over_prob(pricing_mu_t, total_line, sigma_t)
        p_under = 1.0 - p_over
        probabilities["total_over"] = p_over
        probabilities["total_under"] = p_under
        over_row = _side(by_key, "total", "over")
        under_row = _side(by_key, "total", "under")
        over_price = over_row["price"] if over_row else None
        under_price = under_row["price"] if under_row else None
        fair_over, fair_under = fair_two_way(over_price, under_price)
        for side, p, fair, row in (
            ("over", p_over, fair_over, over_row),
            ("under", p_under, fair_under, under_row),
        ):
            if row is None:
                continue
            signal = _make_edge("total", side, total_line, row.get("price"), p, fair, book or "")
            if signal:
                edges.append(signal)

    p_home_ml = moneyline_prob(pricing_mu_m, sigma_m)
    probabilities["moneyline_home"] = p_home_ml
    probabilities["moneyline_away"] = 1.0 - p_home_ml
    if home_ml or away_ml:
        home_price = home_ml["price"] if home_ml else None
        away_price = away_ml["price"] if away_ml else None
        fair_home, fair_away = fair_two_way(home_price, away_price)
        for side, p, fair, row in (
            ("home", p_home_ml, fair_home, home_ml),
            ("away", 1.0 - p_home_ml, fair_away, away_ml),
        ):
            if row is None:
                continue
            signal = _make_edge("moneyline", side, None, row.get("price"), p, fair, book or "")
            if signal:
                edges.append(signal)

    edges.sort(key=lambda e: e["ev"], reverse=True)
    return book, {
        "spread": spread_line,
        "total": total_line,
        "probabilities": probabilities,
        "edges": edges,
        "pricing": {
            "market_blend": MARKET_BLEND,
            "model_margin": model_mu_m,
            "model_total": model_mu_t,
            "market_margin": market_mu_m,
            "market_total": total_line,
            "margin": pricing_mu_m,
            "total": pricing_mu_t,
        },
    }


def predict_game(
    conn: sqlite3.Connection,
    game: dict,
    model: dict,
    elo: dict[int, float],
    rest: dict[int, int],
) -> dict:
    home_id, away_id = int(game["home_id"]), int(game["away_id"])
    injuries = conn.execute(
        "SELECT team_id, player, status, impact FROM injuries WHERE game_id=?",
        (game["id"],),
    ).fetchall()
    injury_home = sum(float(row["impact"]) for row in injuries if int(row["team_id"]) == home_id)
    injury_away = sum(float(row["impact"]) for row in injuries if int(row["team_id"]) == away_id)

    pred = ratings.predict(
        model,
        home_id,
        away_id,
        elo.get(home_id, 1500.0),
        elo.get(away_id, 1500.0),
        rest.get(home_id, 7),
        rest.get(away_id, 7),
        injury_home,
        injury_away,
    )
    odds_rows = latest_odds(conn, game["id"])
    book, market = build_market(pred, odds_rows)

    try:
        injury_suggestions = rapm.suggest_impacts(conn, game["id"])
    except Exception:  # noqa: BLE001 - suggestions are best-effort
        injury_suggestions = []

    details = {
        "model": pred,
        "book": book,
        "lines": {"spread": market["spread"], "total": market["total"]},
        "probabilities": market["probabilities"],
        "pricing": market["pricing"],
        "edges": market["edges"],
        "injury_suggestions": injury_suggestions,
        "components": {
            "elo_home": elo.get(home_id, 1500.0),
            "elo_away": elo.get(away_id, 1500.0),
            "rest_home": rest.get(home_id, 7),
            "rest_away": rest.get(away_id, 7),
            "injury_home": injury_home,
            "injury_away": injury_away,
            "n_games": model["n_games"],
            "model_created_at": model["created_at"],
        },
    }
    cur = conn.execute(
        """
        INSERT INTO predictions(
            game_id, created_at, pred_margin, sigma_margin, pred_total, sigma_total, home_win_prob, details
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            game["id"],
            _now(),
            pred["pred_margin"],
            pred["sigma_margin"],
            pred["pred_total"],
            pred["sigma_total"],
            pred["home_win_prob"],
            json.dumps(details, ensure_ascii=False),
        ),
    )
    return {"prediction_id": int(cur.lastrowid), **pred, **details}


def predict_date(slate_date: str) -> list[dict]:
    conn = db.connect()
    try:
        model = ratings.latest_model(conn)
        if model is None:
            model = ratings.train_and_save(conn)
        placeholders = ",".join("?" for _ in SKIP_STATUSES)
        games = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT * FROM games
                WHERE slate_date=? AND status != 'STATUS_FINAL'
                  AND status NOT IN ({placeholders})
                ORDER BY COALESCE(tipoff, '')
                """,
                (slate_date, *SKIP_STATUSES),
            )
        ]
        elo = ratings.elo_as_of(conn, slate_date)
        team_ids = sorted({int(g["home_id"]) for g in games} | {int(g["away_id"]) for g in games})
        rest = ratings.rest_as_of(conn, slate_date, team_ids)
        results = [predict_game(conn, game, model, elo, rest) for game in games]
        conn.commit()
        return results
    finally:
        conn.close()


def latest_prediction(conn: sqlite3.Connection, game_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM predictions WHERE game_id=? ORDER BY id DESC LIMIT 1",
        (game_id,),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["details"] = json.loads(result["details"])
    return result
