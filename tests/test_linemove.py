"""线移动分析器的单元测试：合成时间序列验证方向判定与统计。"""
import sqlite3

from scripts.linemove_analysis import _line_moves_toward, run


def test_direction_judgement():
    # spread home line going more negative = toward home signal
    assert _line_moves_toward("spread", "home", -5.5, -6.5) == 1
    assert _line_moves_toward("spread", "home", -5.5, -4.5) == -1
    assert _line_moves_toward("spread", "away", -5.5, -6.5) == -1
    assert _line_moves_toward("spread", "away", -5.5, -4.5) == 1
    # total over: higher line = toward over
    assert _line_moves_toward("total", "over", 220.5, 221.5) == 1
    assert _line_moves_toward("total", "over", 220.5, 219.5) == -1
    assert _line_moves_toward("total", "under", 220.5, 221.5) == -1
    assert _line_moves_toward("total", "under", 220.5, 219.5) == 1
    # no move
    assert _line_moves_toward("spread", "home", -5.5, -5.5) == 0


def test_run_against_synthetic_db(tmp_path, monkeypatch):
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
        INSERT INTO teams VALUES (1, 'AAA', 'A A', null), (2, 'BBB', 'B B', null);
        INSERT INTO games VALUES ('g1', '2026-27', 2, '2026-11-04', '2026-11-05T00:00:00+00:00',
          1, 2, NULL, NULL, 'STATUS_SCHEDULED', NULL, NULL, '2026-11-04T00:00:00+00:00');
        -- odds timeline for spread home: -4.5 @ T0, -5.5 @ T1, -6.5 @ T2 (moving toward home)
        INSERT INTO odds_snapshots(game_id, book, market, side, line, price, captured_at) VALUES
          ('g1', 'bookA', 'spread', 'home', -4.5, 1.95, '2026-11-04T10:00:00+00:00'),
          ('g1', 'bookA', 'spread', 'away',  4.5, 1.90, '2026-11-04T10:00:00+00:00'),
          ('g1', 'bookA', 'spread', 'home', -5.5, 1.95, '2026-11-04T12:00:00+00:00'),
          ('g1', 'bookA', 'spread', 'away',  5.5, 1.90, '2026-11-04T12:00:00+00:00'),
          ('g1', 'bookA', 'spread', 'home', -6.5, 1.95, '2026-11-04T14:00:00+00:00'),
          ('g1', 'bookA', 'spread', 'away',  6.5, 1.90, '2026-11-04T14:00:00+00:00');
        """
    )
    details = {
        "edges": [
            {
                "market": "spread", "side": "home", "line": -4.5, "price": 1.95,
                "model_prob": 0.6, "fair_prob": 0.52, "edge": 0.08, "ev": 0.06,
                "kelly": 0.1, "stake_pct": 0.02, "signal": True,
            }
        ]
    }
    import json

    conn.execute(
        "INSERT INTO predictions(game_id, created_at, pred_margin, sigma_margin, pred_total, sigma_total, home_win_prob, details)"
        " VALUES ('g1', '2026-11-04T10:00:00+00:00', 6.0, 11.0, 225.0, 15.0, 0.6, ?)",
        (json.dumps(details),),
    )
    conn.commit()
    conn.close()

    # Point the analyzer at the synthetic DB (both config and db module paths)
    import app.config as config
    import scripts.linemove_analysis as lm

    monkeypatch.setattr(config, "DB_PATH", db_path)

    def _connect(path=None):
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(lm.db, "connect", _connect)
    # 报告写到 tmp，不污染真实 data/
    monkeypatch.setattr(lm, "DATA_DIR", tmp_path)
    rep = lm.run()

    o = rep["overall"]
    assert o["n_signals"] == 1
    assert o["n_with_move"] == 1
    assert o["moved_toward"] == 1  # -4.5 → -6.5 favors the home signal
    assert o["toward_rate"] == 1.0
    assert (tmp_path / "linemove_report.json").exists()
