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

-- 信号日志：每条 bettable 信号第一次出现时记一行（决策时刻的快照），赛后回填
-- 收盘价与结果。用于 A/B 两条改进路线的回测（校准 / 价格闸门）与归因。
-- 四个概率：p_model（原始）、p_calibrated（校准后，暂空）、p_fair（去水公允）、
-- p_implied（成交价隐含 1/price）；三个价格：price_best（当时最优）、
-- price_consensus（共识中位）、price_closing（收盘）。
CREATE TABLE IF NOT EXISTS signal_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id         TEXT NOT NULL,
    slate_date      TEXT,
    market          TEXT NOT NULL,
    side            TEXT NOT NULL,
    line            REAL,
    p_model         REAL,
    p_calibrated    REAL,
    p_fair          REAL,
    p_implied       REAL,
    price_best      REAL,
    price_consensus REAL,
    price_closing   REAL,
    closing_line    REAL,
    ev              REAL,
    edge            REAL,
    bettable        INTEGER NOT NULL DEFAULT 0,
    book            TEXT,
    gated           TEXT,
    result          TEXT,
    profit          REAL,
    captured_at     TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    UNIQUE(game_id, market, side, bettable)
);
CREATE INDEX IF NOT EXISTS idx_signal_log_game ON signal_log(game_id, market);

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

-- 赛中胜率轨迹（研究用）：watch 每轮给进行中的比赛写一行。
-- 唯一键带 period+clock，同一时刻不会重复写入。
CREATE TABLE IF NOT EXISTS live_wp (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id          TEXT NOT NULL,
    period           INTEGER,
    clock            TEXT,
    home_score       INTEGER,
    away_score       INTEGER,
    margin           INTEGER,
    seconds_remaining REAL,
    prior_margin     REAL,
    home_win_prob    REAL NOT NULL,
    captured_at      TEXT NOT NULL,
    UNIQUE(game_id, period, clock)
);
CREATE INDEX IF NOT EXISTS idx_live_wp_game ON live_wp(game_id, id);

-- 滚球盘口原始快照（研究用）：只原样保存，不做语义解释。
-- 滚球线的含义（全场 vs 从现在起、absK 指什么）需要真实 NBA 样本验证后再解析，
-- 先存原始 JSON，保证数据永不失效。
CREATE TABLE IF NOT EXISTS live_odds (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id     TEXT NOT NULL,
    book        TEXT NOT NULL DEFAULT 'hao-tiyu',
    iid         TEXT,
    period      TEXT,
    clock       TEXT,
    score       TEXT,
    market_raw  TEXT NOT NULL,
    detail_raw  TEXT,
    suspended   INTEGER,
    captured_at TEXT NOT NULL,
    UNIQUE(game_id, book, period, clock, score)
);
CREATE INDEX IF NOT EXISTS idx_live_odds_game ON live_odds(game_id, id);

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
    if columns and "ref" not in columns:
        # 台账同步的去重键：手机/静态端导出的注单带字符串 id，坚果云合并用
        conn.execute("ALTER TABLE bets ADD COLUMN ref TEXT")
        conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_bets_ref ON bets(ref) WHERE ref IS NOT NULL")
    # last_seen_at: captured_at 只在报价变化时更新，稳定的线会被误判超龄。
    # 每次抓取都刷新 last_seen_at，新鲜度判断用它而不是 captured_at。
    odds_columns = {row["name"] for row in conn.execute("PRAGMA table_info(odds_snapshots)")}
    if odds_columns and "last_seen_at" not in odds_columns:
        conn.execute("ALTER TABLE odds_snapshots ADD COLUMN last_seen_at TEXT")
        conn.execute(
            "UPDATE odds_snapshots SET last_seen_at = captured_at WHERE last_seen_at IS NULL"
        )
    # 实时比分独立列：不写 home_score/away_score（训练/回测用 score IS NOT NULL
    # 判断「有结果」，写进去会把进行中的比赛当成完赛污染数据）
    games_columns = {row["name"] for row in conn.execute("PRAGMA table_info(games)")}
    if games_columns and "live_home_score" not in games_columns:
        conn.execute("ALTER TABLE games ADD COLUMN live_home_score INTEGER")
    if games_columns and "live_away_score" not in games_columns:
        conn.execute("ALTER TABLE games ADD COLUMN live_away_score INTEGER")

    # 滚球封盘标记：价格全 0.00 / 市场消失即封盘。存成列后分析可以直接
    # WHERE suspended=0 过滤，不必每次重新解析 market_raw。
    live_odds_columns = {
        row["name"] for row in conn.execute("PRAGMA table_info(live_odds)")
    }
    if live_odds_columns and "suspended" not in live_odds_columns:
        conn.execute("ALTER TABLE live_odds ADD COLUMN suspended INTEGER")
        # 回填历史行（只处理还没有标记的行）
        try:
            from .ingest.hao import parse_live_market
            import json as _json

            pending = conn.execute(
                "SELECT id, market_raw FROM live_odds WHERE suspended IS NULL"
            ).fetchall()
            for row in pending:
                try:
                    parsed = parse_live_market(_json.loads(row["market_raw"]))
                    flag = 1 if parsed.get("suspended") else 0
                except (TypeError, ValueError):
                    flag = None
                conn.execute(
                    "UPDATE live_odds SET suspended=? WHERE id=?", (flag, row["id"])
                )
        except Exception:  # noqa: BLE001 - 回填失败不影响启动
            pass
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
    # 信号日志（0.4）：决策时刻的四概率/三价格快照 + 赛后回填的收盘与结果。
    if "signal_log" not in tables:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS signal_log (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                game_id         TEXT NOT NULL,
                slate_date      TEXT,
                market          TEXT NOT NULL,
                side            TEXT NOT NULL,
                line            REAL,
                p_model         REAL,
                p_calibrated    REAL,
                p_fair          REAL,
                p_implied       REAL,
                price_best      REAL,
                price_consensus REAL,
                price_closing   REAL,
                closing_line    REAL,
                ev              REAL,
                edge            REAL,
                bettable        INTEGER NOT NULL DEFAULT 0,
                book            TEXT,
                gated           TEXT,
                result          TEXT,
                profit          REAL,
                captured_at     TEXT NOT NULL,
                created_at      TEXT NOT NULL,
                UNIQUE(game_id, market, side, bettable)
            );
            CREATE INDEX IF NOT EXISTS idx_signal_log_game ON signal_log(game_id, market);
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
