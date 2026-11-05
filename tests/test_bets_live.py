"""bets_analysis 返回实时比赛字段（供收益页标注未结算注单状态）。"""
import sqlite3

from app.api import bets_analysis


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE teams (id INTEGER PRIMARY KEY, abbrev TEXT UNIQUE, name TEXT, logo TEXT);
        CREATE TABLE games (
          id TEXT PRIMARY KEY, season TEXT, season_type INTEGER, slate_date TEXT,
          tipoff TEXT, home_id INTEGER, away_id INTEGER, home_score INTEGER,
          away_score INTEGER, live_home_score INTEGER, live_away_score INTEGER,
          status TEXT, status_detail TEXT, venue TEXT, updated_at TEXT
        );
        CREATE TABLE bets (
          id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT NOT NULL,
          market TEXT NOT NULL, side TEXT NOT NULL, line REAL, price REAL,
          stake REAL NOT NULL, model_prob REAL, ev REAL, placed_at TEXT,
          result TEXT DEFAULT 'open', profit REAL, closing_line REAL,
          closing_price REAL, clv REAL, clv_line REAL, note TEXT, ref TEXT
        );
        INSERT INTO teams VALUES (1, 'LAL', 'Lakers', null), (2, 'BOS', 'Celtics', null);
        INSERT INTO games VALUES ('g1', '2026-27', 2, '2026-11-04', '2026-11-05T00:00:00+00:00',
          1, 2, NULL, NULL, 58, 47, 'STATUS_IN_PROGRESS', 'Q3 10:29', NULL, '2026-11-05T01:00:00+00:00');
        INSERT INTO bets(game_id, market, side, line, price, stake, model_prob, ev, placed_at)
          VALUES ('g1', 'spread', 'home', -2.5, 1.97, 1.0, 0.56, 0.05, '2026-11-04T13:00:00+00:00');
        """
    )
    return conn


def test_bets_analysis_exposes_live_game_state():
    data = bets_analysis(_conn())
    bet = data["bets"][0]
    assert bet["game_status"] == "STATUS_IN_PROGRESS"
    assert bet["live_home_score"] == 58
    assert bet["live_away_score"] == 47
    assert bet["final_home_score"] is None
    assert bet["matchup"] == "BOS @ LAL"


def test_bets_analysis_settled_game_keeps_final_scores():
    conn = _conn()
    conn.execute("UPDATE games SET status='STATUS_FINAL', home_score=112, away_score=104")
    bet = bets_analysis(conn)["bets"][0]
    assert bet["game_status"] == "STATUS_FINAL"
    assert bet["final_home_score"] == 112
    assert bet["final_away_score"] == 104
