"""blend_sweep 的单元测试：合成 DB 验证扫描结构与单调性记录。"""
import sqlite3
from datetime import date, timedelta

import pytest


@pytest.fixture()
def synth_db(tmp_path):
    db_path = tmp_path / "nba.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE teams (id INTEGER PRIMARY KEY, abbrev TEXT UNIQUE, name TEXT, logo TEXT);
        CREATE TABLE games (
          id TEXT PRIMARY KEY, season TEXT, season_type INTEGER, slate_date TEXT,
          tipoff TEXT, home_id INTEGER, away_id INTEGER, home_score INTEGER,
          away_score INTEGER, status TEXT, status_detail TEXT, venue TEXT, updated_at TEXT
        );
        CREATE TABLE odds_snapshots (
          id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT, book TEXT, market TEXT,
          side TEXT, line REAL, price REAL, captured_at TEXT, last_seen_at TEXT,
          source TEXT DEFAULT 'espn'
        );
        CREATE TABLE predictions (
          id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT, created_at TEXT,
          pred_margin REAL, sigma_margin REAL, pred_total REAL, sigma_total REAL,
          home_win_prob REAL, details TEXT
        );
        """
    )
    for t in range(1, 9):
        conn.execute("INSERT INTO teams VALUES (?, ?, ?, null)", (t, f"T{t}", f"Team {t}"))
    d0 = date(2026, 1, 1)
    for i in range(80):
        home = (i % 8) + 1
        away = ((i + 3) % 8) + 1
        if home == away:
            away = (away % 8) + 1
        day = (d0 + timedelta(days=i)).isoformat()
        home_score = 100 + home - away + 3
        conn.execute(
            "INSERT INTO games VALUES (?, '2026-27', 2, ?, ?, ?, ?, ?, ?, 'STATUS_FINAL', NULL, NULL, ?)",
            (f"g{i}", day, f"{day}T00:00:00Z", home, away, home_score, 100, "2026-01-01T00:00:00Z"),
        )
        # 每场两侧行情：价格能去水即可
        conn.execute(
            "INSERT INTO odds_snapshots(game_id, book, market, side, line, price, captured_at) VALUES"
            "(?, 'sbr-fanduel', 'spread', 'home', ?, 1.92, ?),"
            "(?, 'sbr-fanduel', 'spread', 'away', ?, 1.92, ?)",
            (f"g{i}", -(home - away + 3) if home - away + 3 != 0 else -0.5,
             f"{day}T23:00:00Z", f"g{i}",
             (home - away + 3) if home - away + 3 != 0 else 0.5, f"{day}T23:00:00Z"),
        )
        conn.execute(
            "INSERT INTO odds_snapshots(game_id, book, market, side, line, price, captured_at) VALUES"
            "(?, 'sbr-fanduel', 'total', 'over', ?, 1.92, ?),"
            "(?, 'sbr-fanduel', 'total', 'under', ?, 1.92, ?)",
            (f"g{i}", home_score + 100 - 5, f"{day}T23:00:00Z", f"g{i}", home_score + 100 - 5, f"{day}T23:00:00Z"),
        )
    conn.commit()
    conn.close()
    return db_path


def test_blend_sweep_structure(synth_db, monkeypatch):
    import scripts.blend_sweep as bs

    def _connect(path=None):
        c = sqlite3.connect(synth_db)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(bs.db, "connect", _connect)
    monkeypatch.setattr(bs, "DATA_DIR", synth_db.parent)
    rep = bs.run(split=0.7, min_games=50, min_closing=20)

    assert "error" not in rep, rep.get("error")  # 24 test games have closing odds
    assert rep["best_blend"] is not None
    assert set(rep["blends"].keys()) == {"0.0", "0.15", "0.3", "0.45", "0.55", "0.7", "0.85", "1.0"}
    for blend, s in rep["blends"].items():
        assert s["n"] > 0
        for key in ("hit_rate", "roi_at_closing", "mean_edge", "top_quartile_roi"):
            assert key in s
    # 平均 edge 应随 blend 单调上升（模型自信度增加）
    edges = [rep["blends"][k]["mean_edge"] for k in ("0.0", "0.3", "0.55", "1.0")]
    assert edges == sorted(edges), f"mean_edge should increase with blend: {edges}"


def test_blend_sweep_insufficient_closing(synth_db, monkeypatch):
    """没有收盘线的比赛要返回 error 而不是崩。"""
    import scripts.blend_sweep as bs

    def _connect(path=None):
        c = sqlite3.connect(synth_db)
        c.row_factory = sqlite3.Row
        c.execute("DELETE FROM odds_snapshots WHERE book='sbr-fanduel'")
        c.commit()
        return c

    # _connect 被调用两次（run 内部），DELETE 只想执行一次——用幂等 DELETE 即可
    monkeypatch.setattr(bs.db, "connect", _connect)
    monkeypatch.setattr(bs, "DATA_DIR", synth_db.parent)
    rep = bs.run(split=0.7, min_games=50)
    assert "error" in rep
