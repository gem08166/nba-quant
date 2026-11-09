"""回填脚本：插入后必须补上唯一 ref（否则云端同步会留下 NULL ref 并可能撞唯一索引）。"""
import json
import sqlite3

from scripts.backfill_bets import backfill


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE teams (id INTEGER PRIMARY KEY, abbrev TEXT, name TEXT);
        CREATE TABLE games (
            id TEXT PRIMARY KEY, slate_date TEXT, status TEXT,
            home_id INTEGER, away_id INTEGER,
            home_score INTEGER, away_score INTEGER, tipoff TEXT
        );
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY, game_id TEXT, created_at TEXT, details TEXT
        );
        CREATE TABLE bets (
            id INTEGER PRIMARY KEY, game_id TEXT, market TEXT, side TEXT, line REAL,
            price REAL, stake REAL, model_prob REAL, ev REAL, placed_at TEXT,
            result TEXT DEFAULT 'open', profit REAL, closing_line REAL, closing_price REAL,
            clv REAL, clv_line REAL, note TEXT, ref TEXT
        );
        CREATE UNIQUE INDEX idx_bets_ref ON bets(ref) WHERE ref IS NOT NULL;
        CREATE TABLE odds_snapshots (
            id INTEGER PRIMARY KEY, game_id TEXT, book TEXT, market TEXT, side TEXT,
            line REAL, price REAL, captured_at TEXT, source TEXT, last_seen_at TEXT
        );
        INSERT INTO teams VALUES (1,'LAL','Lakers'),(2,'BOS','Celtics');
        INSERT INTO games VALUES ('g1','2026-11-06','STATUS_FINAL',2,1,110,100,'2026-11-06T00:00:00Z');
        """
    )
    edge = {
        "market": "spread",
        "side": "home",
        "line": -2.5,
        "signal": True,
        "bettable": True,
        "ev": 0.08,
        "book": "draftkings",
        "price": 1.95,
        "model_prob": 0.55,
    }
    conn.execute(
        "INSERT INTO predictions(game_id, created_at, details) VALUES(?,?,?)",
        ("g1", "2026-11-05T23:00:00+00:00", json.dumps({"edges": [edge]})),
    )
    conn.commit()
    return conn


def test_backfill_sets_unique_ref():
    conn = _conn()
    stats = backfill(conn, stake=1.0, bettable_only=True)
    assert stats["inserted"] == 1

    row = conn.execute("SELECT id, ref, result, profit FROM bets").fetchone()
    assert row["ref"] == f"srv-{row['id']}"
    assert row["result"] in ("won", "lost", "push")
    # 再跑一次不应重复插入
    again = backfill(conn, stake=1.0, bettable_only=True)
    assert again["inserted"] == 0
    assert conn.execute("SELECT COUNT(*) n FROM bets").fetchone()["n"] == 1
    conn.close()
