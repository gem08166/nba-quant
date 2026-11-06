"""滚球盘口快照：原始保存、队名匹配、去重。"""
import json
import sqlite3

import pytest

from app.ingest import hao


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE teams (id INTEGER PRIMARY KEY, abbrev TEXT, name TEXT);
        CREATE TABLE games (
            id TEXT PRIMARY KEY, slate_date TEXT, status TEXT,
            home_id INTEGER, away_id INTEGER
        );
        CREATE TABLE live_odds (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT NOT NULL, book TEXT NOT NULL DEFAULT 'hao-tiyu',
            iid TEXT, period TEXT, clock TEXT, score TEXT,
            market_raw TEXT NOT NULL, detail_raw TEXT, captured_at TEXT NOT NULL,
            UNIQUE(game_id, book, period, clock, score)
        );
        INSERT INTO teams VALUES (1,'LAL','Los Angeles Lakers'),(2,'BOS','Boston Celtics');
        INSERT INTO games VALUES ('g1','2026-11-06','STATUS_IN_PROGRESS',1,2);
        """
    )
    return conn


MATCH = {
    "iid": 999,
    "inplay": True,
    "home": {"name": "Los Angeles Lakers"},
    "away": {"name": "Boston Celtics"},
    "kickoffTime": 1793923200,
    "state": "",
}


def _detail(clock="07:28", score="41-50"):
    return {
        "detail": {"period": "q3", "time": clock, "score": score},
        "market": {
            "ah": [{"k": "+5.5", "h": "0.89", "a": "0.81", "absK": "+13.5"}],
            "ou": [{"ov": "0.73", "ud": "0.87", "k": "122.5"}],
        },
    }


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(hao, "fetch_inplay_matches", lambda: [MATCH])
    monkeypatch.setattr(hao, "fetch_live_market", lambda iid: _detail())
    return monkeypatch


def test_stores_raw_market(patched):
    conn = _conn()
    stats = hao.store_live_odds(conn, "2026-11-06")
    assert stats["live_nba"] == 1 and stats["matched"] == 1 and stats["written"] == 1
    row = conn.execute("SELECT * FROM live_odds").fetchone()
    assert row["game_id"] == "g1"
    assert row["period"] == "q3" and row["clock"] == "07:28" and row["score"] == "41-50"
    market = json.loads(row["market_raw"])
    # 原始字段（含尚未解释的 absK）必须完整保留
    assert market["ah"][0]["absK"] == "+13.5"
    assert market["ou"][0]["k"] == "122.5"


def test_dedupes_same_clock_and_score(patched):
    conn = _conn()
    hao.store_live_odds(conn, "2026-11-06")
    stats = hao.store_live_odds(conn, "2026-11-06")
    assert stats["written"] == 0
    assert conn.execute("SELECT COUNT(*) n FROM live_odds").fetchone()["n"] == 1


def test_new_clock_creates_new_snapshot(patched):
    conn = _conn()
    hao.store_live_odds(conn, "2026-11-06")
    patched.setattr(hao, "fetch_live_market", lambda iid: _detail(clock="06:52"))
    stats = hao.store_live_odds(conn, "2026-11-06")
    assert stats["written"] == 1
    assert conn.execute("SELECT COUNT(*) n FROM live_odds").fetchone()["n"] == 2


def test_no_live_matches(monkeypatch):
    monkeypatch.setattr(hao, "fetch_inplay_matches", lambda: [])
    stats = hao.store_live_odds(_conn(), "2026-11-06")
    assert stats == {"live_nba": 0, "matched": 0, "written": 0}


def test_unmatched_team_skipped(monkeypatch):
    monkeypatch.setattr(
        hao,
        "fetch_inplay_matches",
        lambda: [{**MATCH, "home": {"name": "Some Other Team"}}],
    )
    monkeypatch.setattr(hao, "fetch_live_market", lambda iid: _detail())
    stats = hao.store_live_odds(_conn(), "2026-11-06")
    assert stats["live_nba"] == 1 and stats["matched"] == 0
