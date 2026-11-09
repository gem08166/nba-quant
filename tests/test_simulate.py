"""模拟注单自动化：赛前先填（open）+ 去重/墓碑保护。"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from app.jobs import simulate


def _conn(tombstones=None, monkeypatch=None):
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
        INSERT INTO teams VALUES (1,'LAL','Lakers'),(2,'BOS','Celtics');
        """
    )
    tip = (datetime.now(timezone.utc) + timedelta(hours=3)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO games VALUES ('g1', ?, 'STATUS_SCHEDULED', 2, 1, NULL, NULL, ?)",
        ("2099-01-01", tip),
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
        ("g1", "2099-01-01T00:00:00+00:00", json.dumps({"edges": [edge]})),
    )
    conn.commit()
    return conn


def test_ensure_sim_bets_creates_open_bet_once(monkeypatch):
    from app.jobs import betssync

    monkeypatch.setattr(betssync, "load_deletions", lambda: set())

    conn = _conn()
    first = simulate.ensure_sim_bets(conn, "2099-01-01")
    assert first["created"] == 1

    row = conn.execute("SELECT * FROM bets").fetchone()
    assert row["result"] == "open"
    assert row["profit"] is None
    assert row["ref"] == simulate.sim_ref("g1", "spread", "home", -2.5)
    assert row["note"].startswith("模拟 可下注")
    assert row["stake"] == 1.0

    # 第二轮不重复
    again = simulate.ensure_sim_bets(conn, "2099-01-01")
    assert again["created"] == 0
    assert conn.execute("SELECT COUNT(*) n FROM bets").fetchone()["n"] == 1


def test_ensure_sim_bets_respects_tombstones(monkeypatch):
    from app.jobs import betssync

    ref = simulate.sim_ref("g1", "spread", "home", -2.5)
    monkeypatch.setattr(betssync, "load_deletions", lambda: {ref})

    conn = _conn()
    stats = simulate.ensure_sim_bets(conn, "2099-01-01")
    assert stats["created"] == 0
    assert conn.execute("SELECT COUNT(*) n FROM bets").fetchone()["n"] == 0


def test_sim_ref_is_stable_and_distinct():
    a = simulate.sim_ref("g1", "spread", "home", -2.5)
    b = simulate.sim_ref("g1", "spread", "home", -2.5)
    c = simulate.sim_ref("g1", "spread", "home", -3.5)
    assert a == b
    assert a != c
    assert a.startswith("sim-") and len(a) == 16


def test_bet_kind_treats_sim_as_backfill():
    from app.jobs.settle import bet_kind, bet_source

    bet = {"note": "模拟 可下注 · draftkings · EV +8.0% · 2026-11-09"}
    assert bet_kind(bet) == "backfill"
    assert bet_source(bet) == "sim"


def _finished_conn():
    import sqlite3

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


def test_backfill_finished_uses_stable_sim_ref():
    from scripts.backfill_bets import backfill

    conn = _finished_conn()
    stats = backfill(conn, bettable_only=True, sim_only=True, tombstones=set())
    assert stats["inserted"] == 1

    row = conn.execute("SELECT ref, note, result FROM bets").fetchone()
    assert row["ref"] == simulate.sim_ref("g1", "spread", "home", -2.5)
    assert row["note"].startswith("模拟 可下注")
    assert row["result"] in ("won", "lost", "push")


def test_backfill_finished_respects_tombstones():
    from scripts.backfill_bets import backfill

    conn = _finished_conn()
    ref = simulate.sim_ref("g1", "spread", "home", -2.5)
    stats = backfill(conn, bettable_only=True, sim_only=True, tombstones={ref})
    assert stats["inserted"] == 0
    assert stats["skipped_tombstone"] == 1
    assert conn.execute("SELECT COUNT(*) n FROM bets").fetchone()["n"] == 0
