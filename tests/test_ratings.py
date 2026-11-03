from datetime import date as date_cls, timedelta

from app.models.ratings import compute_elo, compute_rest, fit


def _game(game_id, day, home, away, home_score, away_score):
    return {
        "id": game_id,
        "slate_date": day,
        "tipoff": f"{day}T00:00Z",
        "home_id": home,
        "away_id": away,
        "home_score": home_score,
        "away_score": away_score,
    }


def test_elo_pregame_has_no_lookahead():
    games = [
        _game("g1", "2026-01-01", 1, 2, 110, 100),
        _game("g2", "2026-01-02", 1, 3, 105, 99),
    ]
    pregame, final = compute_elo(games)
    assert pregame["g1"] == (1500.0, 1500.0)
    assert pregame["g2"][0] > 1500.0  # team 1 won game 1
    assert final[1] > final[3]


def test_rest_days_capped():
    games = [
        _game("g1", "2026-01-01", 1, 2, 110, 100),
        _game("g2", "2026-01-03", 1, 3, 105, 99),
    ]
    rest = compute_rest(games)
    assert rest["g1"] == (7, 7)
    assert rest["g2"] == (2, 7)


def _seed_db(games):
    import sqlite3

    from app import db

    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(db.SCHEMA)
    for game in games:
        conn.execute(
            """
            INSERT INTO games(id, season, season_type, slate_date, tipoff,
                              home_id, away_id, home_score, away_score,
                              status, updated_at)
            VALUES(?, '2025-26', 2, ?, ?, ?, ?, ?, ?, 'final', 'x')
            """,
            (
                game["id"], game["slate_date"], game["tipoff"], game["home_id"],
                game["away_id"], game["home_score"], game["away_score"],
            ),
        )
    conn.commit()
    return conn


def test_total_regime_shift_pulls_intercept_to_recent_average():
    """Mid-season scoring drop: recalibration must track the recent level."""
    # 60 low-scoring games, a 20-day gap, then 30 high-scoring games so the
    # 30-day regime window contains only the new (high-scoring) regime.
    games = []
    gid = 0
    day = date_cls(2026, 1, 1)
    for _ in range(60):
        games.append(_game(f"g{gid}", day.isoformat(), 1 + (gid % 8), 1 + ((gid + 1) % 8), 104, 100))
        gid += 1
        day += timedelta(days=1)
    day += timedelta(days=20)
    for _ in range(30):
        games.append(_game(f"g{gid}", day.isoformat(), 1 + (gid % 8), 1 + ((gid + 1) % 8), 112, 106))
        gid += 1
        day += timedelta(days=1)

    conn = _seed_db(games)
    try:
        model = fit(conn)
        recent_actual = sum(g["home_score"] + g["away_score"] for g in games[-30:]) / 30
        recent_pred = sum(
            model["total_intercept"]
            + model["teams"][str(g["home_id"])]["env"]
            + model["teams"][str(g["away_id"])]["env"]
            for g in games[-30:]
        ) / 30
        assert model["total_regime_shift"] > 0.0
        assert abs(recent_pred - recent_actual) < 2.0
    finally:
        conn.close()


def test_total_regime_skipped_across_offseason():
    """A long gap since the last game must disable the shift (opening week)."""
    games = []
    gid = 0
    day = date_cls(2026, 1, 1)
    for _ in range(80):
        games.append(_game(f"g{gid}", day.isoformat(), 1 + (gid % 8), 1 + ((gid + 1) % 8), 104, 100))
        gid += 1
        day += timedelta(days=1)

    conn = _seed_db(games)
    try:
        # predict for a date ~5 months after the last game: skip recalibration
        model = fit(conn, as_of="2026-06-10")
        assert model["total_regime_shift"] == 0.0
    finally:
        conn.close()
