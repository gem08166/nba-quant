"""滚球阶段不推送赛前信号：collect_signal_items 必须排除已开赛的比赛。"""
import json
import sqlite3
from datetime import datetime, timedelta, timezone

from app.jobs import notify


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE teams (id INTEGER PRIMARY KEY, abbrev TEXT, name TEXT);
        CREATE TABLE games (
            id TEXT PRIMARY KEY, slate_date TEXT, status TEXT, tipoff TEXT,
            home_id INTEGER, away_id INTEGER
        );
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY, game_id TEXT, created_at TEXT,
            pred_margin REAL, sigma_margin REAL, pred_total REAL, sigma_total REAL,
            home_win_prob REAL, details TEXT
        );
        INSERT INTO teams VALUES (1,'LAL','Lakers'),(2,'BOS','Celtics');
        """
    )
    now = datetime.now(timezone.utc)
    started = (now - timedelta(minutes=30)).isoformat(timespec="seconds")
    upcoming = (now + timedelta(hours=3)).isoformat(timespec="seconds")
    conn.execute(
        "INSERT INTO games VALUES ('g_started','2026-11-06','STATUS_IN_PROGRESS',?,1,2)",
        (started,),
    )
    conn.execute(
        "INSERT INTO games VALUES ('g_upcoming','2026-11-06','STATUS_SCHEDULED',?,1,2)",
        (upcoming,),
    )
    edge = {
        "market": "spread",
        "side": "home",
        "line": -2.5,
        "price": 1.95,
        "ev": 0.08,
        "signal": True,
        "bettable": True,
    }
    for gid in ("g_started", "g_upcoming"):
        conn.execute(
            "INSERT INTO predictions (game_id, created_at, pred_margin, sigma_margin,"
            " pred_total, sigma_total, home_win_prob, details) VALUES (?,?,?,?,?,?,?,?)",
            (gid, started, 3.0, 11.0, 220.0, 15.0, 0.55, json.dumps({"edges": [edge]})),
        )
    return conn


def test_started_game_not_collected():
    """已开赛（tipoff 已过 / 状态 in progress）的比赛不能进推送队列。"""
    items = notify.collect_signal_items(_conn(), "2026-11-06")
    ids = {game["id"] for game, _ in items}
    assert "g_started" not in ids
    assert ids == {"g_upcoming"}


def test_scheduled_but_tipoff_passed_not_collected():
    """状态还没刷新成 in progress、但开赛时间已过的比赛同样不能推。"""
    conn = _conn()
    past = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat(timespec="seconds")
    conn.execute("UPDATE games SET tipoff=? WHERE id='g_upcoming'", (past,))
    items = notify.collect_signal_items(conn, "2026-11-06")
    assert items == []
