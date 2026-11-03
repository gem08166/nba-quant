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


def _devig_pair(price_a: float | None, price_b: float | None) -> tuple[float, float] | None:
    """乘法去水：返回 (a 侧公允概率, b 侧公允概率)。缺一边返回 None。"""
    if not price_a or not price_b or price_a <= 1.0 or price_b <= 1.0:
        return None
    inv_a, inv_b = 1.0 / price_a, 1.0 / price_b
    total = inv_a + inv_b
    return inv_a / total, inv_b / total


def _mode(values: list[float]) -> float | None:
    """最常见线（并列取中位数）：多家书源让分/大小线可能不同。"""
    if not values:
        return None
    counts: dict[float, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    best = max(counts.values())
    tied = sorted(v for v, c in counts.items() if c == best)
    mid = len(tied) // 2
    return tied[mid] if len(tied) % 2 else (tied[mid - 1] + tied[mid]) / 2


def consensus_market(odds_rows: list[dict]) -> dict:
    """多书共识：对每家书源分别去水后取平均公允概率；线取众数；每侧取共识线下的最优价。

    返回 {spread: {line, home_price, away_price, fair_home, fair_away, n_books},
          total: {...over/under...}, moneyline: {home_price, away_price, fair_home, fair_away, n_books}}
    单书源时退化为该书源的去水结果（与旧逻辑兼容）。
    """
    books: dict[str, dict[tuple[str, str], dict]] = {}
    for row in odds_rows:
        if not row.get("price") or row["price"] <= 1.0:
            continue
        books.setdefault(row["book"], {})[(row["market"], row["side"])] = row

    result: dict[str, dict] = {}

    # --- spread（home 线） ---
    lines: list[float] = []
    fair_h, fair_a, best_h, best_a = [], [], None, None
    for book, by_key in books.items():
        h, a = by_key.get(("spread", "home")), by_key.get(("spread", "away"))
        if not h or not a or h.get("line") is None or a.get("line") is None:
            continue
        line_h, line_a = float(h["line"]), float(a["line"])
        if abs(line_h + line_a) > 1e-9:  # 两边线应互为相反数
            continue
        pair = _devig_pair(h["price"], a["price"])
        if not pair:
            continue
        lines.append(line_h)
        fair_h.append(pair[0])
        fair_a.append(pair[1])
    if lines:
        consensus_line = _mode(lines)
        for book, by_key in books.items():
            h, a = by_key.get(("spread", "home")), by_key.get(("spread", "away"))
            if not h or not a or h.get("line") is None:
                continue
            if abs(float(h["line"]) - consensus_line) < 1e-9:
                best_h = max(best_h, h["price"]) if best_h else h["price"]
                best_a = max(best_a, a["price"]) if best_a else a["price"]
        result["spread"] = {
            "line": consensus_line,
            "home_price": best_h,
            "away_price": best_a,
            "fair_home": sum(fair_h) / len(fair_h),
            "fair_away": sum(fair_a) / len(fair_a),
            "n_books": len(lines),
        }

    # --- total ---
    lines, fair_o, fair_u, best_o, best_u = [], [], [], None, None
    for book, by_key in books.items():
        o, u = by_key.get(("total", "over")), by_key.get(("total", "under"))
        if not o or not u or o.get("line") is None or u.get("line") is None:
            continue
        line_o, line_u = float(o["line"]), float(u["line"])
        if abs(line_o - line_u) > 1e-9:
            continue
        pair = _devig_pair(o["price"], u["price"])
        if not pair:
            continue
        lines.append(line_o)
        fair_o.append(pair[0])
        fair_u.append(pair[1])
    if lines:
        consensus_line = _mode(lines)
        for book, by_key in books.items():
            o, u = by_key.get(("total", "over")), by_key.get(("total", "under"))
            if not o or not u or o.get("line") is None:
                continue
            if abs(float(o["line"]) - consensus_line) < 1e-9:
                best_o = max(best_o, o["price"]) if best_o else o["price"]
                best_u = max(best_u, u["price"]) if best_u else u["price"]
        result["total"] = {
            "line": consensus_line,
            "over_price": best_o,
            "under_price": best_u,
            "fair_over": sum(fair_o) / len(fair_o),
            "fair_under": sum(fair_u) / len(fair_u),
            "n_books": len(lines),
        }

    # --- moneyline ---
    fair_h, fair_a, best_h, best_a = [], [], None, None
    for book, by_key in books.items():
        h, a = by_key.get(("moneyline", "home")), by_key.get(("moneyline", "away"))
        if not h or not a:
            continue
        pair = _devig_pair(h["price"], a["price"])
        if not pair:
            continue
        fair_h.append(pair[0])
        fair_a.append(pair[1])
        best_h = max(best_h, h["price"]) if best_h else h["price"]
        best_a = max(best_a, a["price"]) if best_a else a["price"]
    if fair_h:
        result["moneyline"] = {
            "home_price": best_h,
            "away_price": best_a,
            "fair_home": sum(fair_h) / len(fair_h),
            "fair_away": sum(fair_a) / len(fair_a),
            "n_books": len(fair_h),
        }
    return result


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
    """定价：多书共识（多书源各自去水取平均公允概率、线取众数、价取共识线下最优），
    再与模型按 MARKET_BLEND 融合。只有单书源时自动退化为该书源。"""
    cons = consensus_market(odds_rows)
    book = pick_book(odds_rows)
    by_key = {(row["market"], row["side"]): row for row in odds_rows if row["book"] == book}
    model_mu_m = pred["pred_margin"]
    model_mu_t = pred["pred_total"]
    sigma_m = pred["sigma_margin"]
    sigma_t = pred["sigma_total"]

    spread_cons = cons.get("spread")
    spread_line = spread_cons["line"] if spread_cons else None
    total_cons = cons.get("total")
    total_line = total_cons["line"] if total_cons else None

    home_spread = _side(by_key, "spread", "home")
    away_spread = _side(by_key, "spread", "away")
    over_row = _side(by_key, "total", "over")
    under_row = _side(by_key, "total", "under")
    home_ml = _side(by_key, "moneyline", "home")
    away_ml = _side(by_key, "moneyline", "away")

    # Blend the model with the consensus market line. Single soft book lines are
    # noisy and early-season team ratings are stale, so a pure model would
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
        if spread_cons and spread_cons.get("fair_home") is not None:
            fair_home, fair_away = spread_cons["fair_home"], spread_cons["fair_away"]
        else:
            fair_home, fair_away = fair_two_way(
                home_spread["price"] if home_spread else None,
                away_spread["price"] if away_spread else None,
            )
        # 成交价取共识线下各侧最优价（论文研究口径：你能拿到的最优价）
        home_price = (spread_cons or {}).get("home_price") or (home_spread["price"] if home_spread else None)
        away_price = (spread_cons or {}).get("away_price") or (away_spread["price"] if away_spread else None)
        for side, p, fair, price, side_line in (
            ("home", p_home, fair_home, home_price, spread_line),
            ("away", p_away, fair_away, away_price, -spread_line),
        ):
            if price is None:
                continue
            signal = _make_edge("spread", side, side_line, price, p, fair, book or "")
            if signal:
                edges.append(signal)

    if total_line is not None:
        p_over = over_prob(pricing_mu_t, total_line, sigma_t)
        p_under = 1.0 - p_over
        probabilities["total_over"] = p_over
        probabilities["total_under"] = p_under
        if total_cons and total_cons.get("fair_over") is not None:
            fair_over, fair_under = total_cons["fair_over"], total_cons["fair_under"]
        else:
            fair_over, fair_under = fair_two_way(
                over_row["price"] if over_row else None,
                under_row["price"] if under_row else None,
            )
        over_price = (total_cons or {}).get("over_price") or (over_row["price"] if over_row else None)
        under_price = (total_cons or {}).get("under_price") or (under_row["price"] if under_row else None)
        for side, p, fair, price in (
            ("over", p_over, fair_over, over_price),
            ("under", p_under, fair_under, under_price),
        ):
            if price is None:
                continue
            signal = _make_edge("total", side, total_line, price, p, fair, book or "")
            if signal:
                edges.append(signal)

    p_home_ml = moneyline_prob(pricing_mu_m, sigma_m)
    probabilities["moneyline_home"] = p_home_ml
    probabilities["moneyline_away"] = 1.0 - p_home_ml
    if home_ml or away_ml:
        ml_cons = cons.get("moneyline") or {}
        if ml_cons.get("fair_home") is not None:
            fair_home, fair_away = ml_cons["fair_home"], ml_cons["fair_away"]
        else:
            fair_home, fair_away = fair_two_way(
                home_ml["price"] if home_ml else None,
                away_ml["price"] if away_ml else None,
            )
        home_price = ml_cons.get("home_price") or (home_ml["price"] if home_ml else None)
        away_price = ml_cons.get("away_price") or (away_ml["price"] if away_ml else None)
        for side, p, fair, price in (
            ("home", p_home_ml, fair_home, home_price),
            ("away", 1.0 - p_home_ml, fair_away, away_price),
        ):
            if price is None:
                continue
            signal = _make_edge("moneyline", side, None, price, p, fair, book or "")
            if signal:
                edges.append(signal)

    edges.sort(key=lambda e: e["ev"], reverse=True)
    return book, {
        "spread": spread_line,
        "total": total_line,
        "probabilities": probabilities,
        "edges": edges,
        "consensus": {
            "n_books_spread": (spread_cons or {}).get("n_books", 0),
            "n_books_total": (total_cons or {}).get("n_books", 0),
            "n_books_ml": (cons.get("moneyline") or {}).get("n_books", 0),
        },
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
        "consensus": market.get("consensus"),
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
