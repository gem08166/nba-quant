"""历史注单回填：只取首次出现的信号、按当时可拿价格、用终局结算。"""
import json
import sqlite3

import pytest

from scripts.backfill_bets import backfill


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE teams (id INTEGER PRIMARY KEY, abbrev TEXT, name TEXT);
        CREATE TABLE games (
            id TEXT PRIMARY KEY, slate_date TEXT, tipoff TEXT,
            home_id INTEGER, away_id INTEGER, home_score INTEGER, away_score INTEGER
        );
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT, created_at TEXT, details TEXT
        );
        CREATE TABLE odds_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT, book TEXT,
            market TEXT, side TEXT, line REAL, price REAL, captured_at TEXT
        );
        CREATE TABLE bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT, market TEXT, side TEXT,
            line REAL, price REAL, stake REAL, model_prob REAL, ev REAL,
            clv REAL, clv_line REAL, placed_at TEXT, note TEXT, result TEXT, profit REAL, ref TEXT
        );
        INSERT INTO teams VALUES (1,'LAL','Lakers'),(2,'BOS','Celtics');
        -- 主队赢 5 分：LAL 110 : BOS 105
        INSERT INTO games VALUES ('g1','2026-11-04','2026-11-05T00:00Z',1,2,110,105);
        INSERT INTO odds_snapshots(game_id, book, market, side, line, price, captured_at)
          VALUES ('g1','sbr','spread','home',-3.5,1.95,'2026-11-04T20:00:00+00:00');
        """
    )
    edge = {
        "market": "spread",
        "side": "home",
        "line": -3.5,
        "price": 1.90,
        "book": "sbr",
        "model_prob": 0.57,
        "ev": 0.083,
        "signal": True,
    }
    # 同一信号重复出现两次（只应回填一次）
    for created in ("2026-11-04T20:00:00+00:00", "2026-11-04T21:00:00+00:00"):
        conn.execute(
            "INSERT INTO predictions(game_id, created_at, details) VALUES (?,?,?)",
            ("g1", created, json.dumps({"edges": [edge]})),
        )
    conn.commit()
    return conn


def test_backfills_once_and_settles():
    conn = _conn()
    stats = backfill(conn, stake=1.0)
    assert stats["inserted"] == 1
    assert stats["wins"] == 1 and stats["losses"] == 0

    row = conn.execute("SELECT * FROM bets").fetchone()
    # 用当时可拿到的报价 1.95，而不是信号自带的 1.90
    assert row["price"] == pytest.approx(1.95)
    assert row["result"] == "won"
    assert row["profit"] == pytest.approx(0.95)
    assert row["note"].startswith("回填")
    assert "滚球" not in row["note"]


def test_idempotent_on_second_run():
    conn = _conn()
    backfill(conn, stake=1.0)
    stats = backfill(conn, stake=1.0)
    assert stats["inserted"] == 0
    assert stats["skipped_dupe"] == 1
    assert conn.execute("SELECT COUNT(*) n FROM bets").fetchone()["n"] == 1


def test_dry_run_writes_nothing():
    conn = _conn()
    stats = backfill(conn, stake=1.0, dry_run=True)
    assert stats["inserted"] == 1 and stats["dry_run"] is True
    assert conn.execute("SELECT COUNT(*) n FROM bets").fetchone()["n"] == 0


def test_date_range_filter():
    conn = _conn()
    assert backfill(conn, start="2026-11-05")["inserted"] == 0
    assert backfill(conn, end="2026-11-03")["inserted"] == 0


def _conn_multi():
    """同一场：大小两条不同盘口 + 让分 + 胜负（相关）。"""
    conn = _conn()
    conn.execute("DELETE FROM predictions")
    edges = [
        {"market": "total", "side": "over", "line": 220.5, "price": 1.90, "model_prob": 0.55, "ev": 0.05, "signal": True},
        {"market": "total", "side": "over", "line": 221.5, "price": 1.92, "model_prob": 0.56, "ev": 0.07, "signal": True},
        {"market": "spread", "side": "home", "line": -3.5, "price": 1.95, "model_prob": 0.57, "ev": 0.08, "signal": True},
        {"market": "moneyline", "side": "home", "line": None, "price": 1.75, "model_prob": 0.60, "ev": 0.12, "signal": True},
    ]
    conn.execute(
        "INSERT INTO predictions(game_id, created_at, details) VALUES (?,?,?)",
        ("g1", "2026-11-04T20:00:00+00:00", json.dumps({"edges": edges})),
    )
    conn.commit()
    return conn


def test_strict_dedupes_line_drift_and_correlated_markets():
    """严格口径：同市场只留首次出现；让分/胜负二选一（留 EV 高的）。"""
    conn = _conn_multi()
    stats = backfill(conn, stake=1.0)
    assert stats["inserted"] == 2  # 大小 1 条 + 胜负 1 条
    rows = conn.execute("SELECT market, side, line FROM bets ORDER BY market").fetchall()
    markets = {r["market"] for r in rows}
    assert markets == {"total", "moneyline"}
    total = [r for r in rows if r["market"] == "total"][0]
    assert total["line"] == 220.5  # 首次出现的盘口


def test_raw_mode_keeps_everything():
    conn = _conn_multi()
    stats = backfill(conn, stake=1.0, strict=False)
    assert stats["inserted"] == 4

