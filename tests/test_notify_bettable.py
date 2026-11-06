"""推送只发通过「可下注阀门」的信号（bettable=True）。

signal 只代表模型算出正 EV；bettable 才代表价格真实可拿、共识够深。
软书价、浅共识、分歧闸门、同场相关降级的观察项不能进推送队列。
"""
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
    upcoming = (now + timedelta(hours=3)).isoformat(timespec="seconds")
    cases = {
        # 通过阀门：真实可拿 + 共识够深
        "g_bettable": {"signal": True, "bettable": True},
        # 观察项：浅共识/软书，不应推送
        "g_shallow": {"signal": True, "bettable": False},
        # 被闸门拦下：signal=False
        "g_gated": {"signal": False, "bettable": False},
    }
    for gid, flags in cases.items():
        conn.execute(
            "INSERT INTO games VALUES (?,?,?,?,1,2)",
            (gid, "2026-11-06", "STATUS_SCHEDULED", upcoming),
        )
        edge = {
            "market": "spread",
            "side": "home",
            "line": -2.5,
            "price": 1.95,
            "book": "DraftKings",
            "model_prob": 0.58,
            "fair_prob": 0.52,
            "edge": 0.06,
            "ev": 0.13,
            "stake_pct": 0.02,
            **flags,
        }
        if not flags["bettable"]:
            edge["bettable_note"] = "共识书源数 2 < 3（定价基准不可靠）"
        conn.execute(
            "INSERT INTO predictions (game_id, created_at, pred_margin, sigma_margin,"
            " pred_total, sigma_total, home_win_prob, details) VALUES (?,?,?,?,?,?,?,?)",
            (
                gid,
                now.isoformat(timespec="seconds"),
                3.0,
                11.0,
                220.0,
                15.0,
                0.55,
                json.dumps({"edges": [edge]}),
            ),
        )
    return conn


def test_only_bettable_collected_by_default():
    items = notify.collect_signal_items(_conn(), "2026-11-06")
    ids = {game["id"] for game, _ in items}
    assert ids == {"g_bettable"}


def test_raw_mode_still_returns_observations():
    items = notify.collect_signal_items(_conn(), "2026-11-06", bettable_only=False)
    ids = {game["id"] for game, _ in items}
    # 观察项可统计，但 gated（signal=False）永远不算信号
    assert ids == {"g_bettable", "g_shallow"}


def test_format_signal_marks_bettable_and_book():
    items = notify.collect_signal_items(_conn(), "2026-11-06")
    _, edge = items[0]
    game = {"id": "g_bettable", "tipoff": "2026-11-07T00:00:00+00:00",
            "home_abbrev": "LAL", "away_abbrev": "BOS"}
    text = notify.format_signal(game, edge)
    assert "可下注" in text
    assert "DraftKings" in text
    assert "EV +13.0%" in text


def test_send_dedupes_and_records_only_bettable(monkeypatch):
    monkeypatch.setattr(notify.db, "init_db", lambda conn: None)
    monkeypatch.setattr(notify, "send", lambda title, text: True)
    conn = _conn()
    conn.executescript(
        """
        CREATE TABLE notifications (
            game_id TEXT, market TEXT, side TEXT, line REAL, price REAL, sent_at TEXT,
            UNIQUE(game_id, market, side, line, price)
        );
        """
    )
    items = notify.collect_signal_items(conn, "2026-11-06")
    sent = notify.send_new_signals(conn, items)
    assert sent == 1
    rows = conn.execute("SELECT game_id FROM notifications").fetchall()
    assert [row["game_id"] for row in rows] == ["g_bettable"]
    # 再来一次：已推送过，幂等
    assert notify.send_new_signals(conn, items) == 0
