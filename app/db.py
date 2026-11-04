from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .config import DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS teams (
    id          INTEGER PRIMARY KEY,
    abbrev      TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    logo        TEXT
);

CREATE TABLE IF NOT EXISTS games (
    id            TEXT PRIMARY KEY,
    season        TEXT NOT NULL,
    season_type   INTEGER,
    slate_date    TEXT NOT NULL,
    tipoff        TEXT,
    home_id       INTEGER NOT NULL,
    away_id       INTEGER NOT NULL,
    home_score    INTEGER,
    away_score    INTEGER,
    status        TEXT NOT NULL,
    status_detail TEXT,
    venue         TEXT,
    updated_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_games_date ON games(slate_date);
CREATE INDEX IF NOT EXISTS idx_games_home ON games(home_id);
CREATE INDEX IF NOT EXISTS idx_games_away ON games(away_id);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id     TEXT NOT NULL,
    book        TEXT NOT NULL,
    market      TEXT NOT NULL,
    side        TEXT NOT NULL,
    line        REAL,
    price       REAL,
    captured_at TEXT NOT NULL,
    last_seen_at TEXT,
    source      TEXT NOT NULL DEFAULT 'espn',
    UNIQUE(game_id, book, market, side, line, price, captured_at)
);
CREATE INDEX IF NOT EXISTS idx_odds_game ON odds_snapshots(game_id, captured_at);

CREATE TABLE IF NOT EXISTS injuries (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id     TEXT NOT NULL,
    team_id     INTEGER NOT NULL,
    player      TEXT NOT NULL,
    status      TEXT,
    detail      TEXT,
    impact      REAL NOT NULL DEFAULT 0,
    captured_at TEXT NOT NULL,
    UNIQUE(game_id, team_id, player, status)
);
CREATE INDEX IF NOT EXISTS idx_injuries_game ON injuries(game_id);

CREATE TABLE IF NOT EXISTS predictions (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id        TEXT NOT NULL,
    created_at     TEXT NOT NULL,
    pred_margin    REAL NOT NULL,
    sigma_margin   REAL NOT NULL,
    pred_total     REAL NOT NULL,
    sigma_total    REAL NOT NULL,
    home_win_prob  REAL NOT NULL,
    details        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_predictions_game ON predictions(game_id, created_at);

CREATE TABLE IF NOT EXISTS models (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    n_games      INTEGER NOT NULL,
    sigma_margin REAL NOT NULL,
    sigma_total  REAL NOT NULL,
    params       TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bets (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id       TEXT NOT NULL,
    market        TEXT NOT NULL,
    side          TEXT NOT NULL,
    line          REAL,
    price         REAL NOT NULL,
    stake         REAL NOT NULL,
    model_prob    REAL NOT NULL,
    ev            REAL NOT NULL,
    placed_at     TEXT NOT NULL,
    result        TEXT NOT NULL DEFAULT 'open',
    profit        REAL,
    closing_line  REAL,
    closing_price REAL,
    clv           REAL,
    clv_line      REAL,
    note          TEXT
);

CREATE TABLE IF NOT EXISTS notifications (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id   TEXT NOT NULL,
    market    TEXT NOT NULL,
    side      TEXT NOT NULL,
    line      REAL,
    price     REAL,
    sent_at   TEXT NOT NULL,
    UNIQUE(game_id, market, side, line, price)
);

CREATE TABLE IF NOT EXISTS players (
    id         INTEGER PRIMARY KEY,  -- ESPN athlete id
    name       TEXT NOT NULL,
    team_id    INTEGER,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pbp_plays (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id    TEXT NOT NULL,
    seq        INTEGER NOT NULL,
    period     INTEGER,
    clock      TEXT,
    play_type  TEXT,
    team_id    INTEGER,
    player_in  INTEGER,
    player_out INTEGER,
    home_score INTEGER,
    away_score INTEGER,
    UNIQUE(game_id, seq)
);
CREATE INDEX IF NOT EXISTS idx_pbp_game ON pbp_plays(game_id);

CREATE TABLE IF NOT EXISTS starters (
    game_id   TEXT NOT NULL,
    team_id   INTEGER NOT NULL,
    player_id INTEGER NOT NULL,
    PRIMARY KEY(game_id, team_id, player_id)
);

CREATE TABLE IF NOT EXISTS rapm_models (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    n_games    INTEGER,
    n_stints   INTEGER,
    params     TEXT NOT NULL
);

-- HoopsAPI 比赛映射：hoops 的 game_id ↔ 本库（ESPN）game_id。
-- 比赛完赛后用 hoops_id 从归档拉多书收盘价。
CREATE TABLE IF NOT EXISTS hoops_games (
    hoops_id    TEXT PRIMARY KEY,
    espn_game_id TEXT NOT NULL,
    start_time  TEXT,
    archived    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_hoops_unarchived ON hoops_games(archived);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Lightweight migration for databases created before 0.2/0.3."""
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(bets)")}
    if columns and "profit" not in columns:
        conn.execute("ALTER TABLE bets ADD COLUMN profit REAL")
    if columns and "clv_line" not in columns:
        conn.execute("ALTER TABLE bets ADD COLUMN clv_line REAL")
    # last_seen_at: captured_at 只在报价变化时更新，稳定的线会被误判超龄。
    # 每次抓取都刷新 last_seen_at，新鲜度判断用它而不是 captured_at。
    odds_columns = {row["name"] for row in conn.execute("PRAGMA table_info(odds_snapshots)")}
    if odds_columns and "last_seen_at" not in odds_columns:
        conn.execute("ALTER TABLE odds_snapshots ADD COLUMN last_seen_at TEXT")
        conn.execute(
            "UPDATE odds_snapshots SET last_seen_at = captured_at WHERE last_seen_at IS NULL"
        )
    conn.commit()
    # Tables added in 0.3 (players / PBP / RAPM). CREATE IF NOT EXISTS is
    # idempotent, but skip the DDL entirely when the tables already exist:
    # it is a write transaction on every connect and can hit "database is
    # locked" while another process (backtest / watch) holds the lock.
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    if not {"players", "pbp_plays", "starters", "rapm_models", "hoops_games"} <= tables:
        conn.executescript(        """
        CREATE TABLE IF NOT EXISTS players (
            id         INTEGER PRIMARY KEY,
            name       TEXT NOT NULL,
            team_id    INTEGER,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pbp_plays (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id    TEXT NOT NULL,
            seq        INTEGER NOT NULL,
            period     INTEGER,
            clock      TEXT,
            play_type  TEXT,
            team_id    INTEGER,
            player_in  INTEGER,
            player_out INTEGER,
            home_score INTEGER,
            away_score INTEGER,
            UNIQUE(game_id, seq)
        );
        CREATE INDEX IF NOT EXISTS idx_pbp_game ON pbp_plays(game_id);
        CREATE TABLE IF NOT EXISTS starters (
            game_id   TEXT NOT NULL,
            team_id   INTEGER NOT NULL,
            player_id INTEGER NOT NULL,
            PRIMARY KEY(game_id, team_id, player_id)
        );
        CREATE TABLE IF NOT EXISTS rapm_models (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            n_games    INTEGER,
            n_stints   INTEGER,
            params     TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS hoops_games (
            hoops_id     TEXT PRIMARY KEY,
            espn_game_id TEXT NOT NULL,
            start_time   TEXT,
            archived     INTEGER NOT NULL DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_hoops_unarchived ON hoops_games(archived);
        """
    )
    conn.commit()


def connect(path: Path | str = DB_PATH) -> sqlite3.Connection:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # check_same_thread=False: FastAPI runs sync endpoints on a thread pool,
    # so a request's connection may be created and used on different threads.
    # Each request still gets its own connection (see api.get_conn).
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    _migrate(conn)
    return conn


def init_db(conn: sqlite3.Connection | None = None) -> None:
    own = conn is None
    conn = conn or connect()
    try:
        conn.executescript(SCHEMA)
        conn.commit()
    finally:
        if own:
            conn.close()


@contextmanager
def tx(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
