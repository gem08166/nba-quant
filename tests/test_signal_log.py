"""signal_log 的写入与赛后结算（A/B 回测的数据底座）。"""
import sqlite3

from app.db import SCHEMA
from app.jobs.predict import log_signals
from app.jobs.settle import settle_signal_log


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    conn.execute(
        "INSERT INTO teams(id, abbrev, name) VALUES (1, 'AAA', 'A'), (2, 'BBB', 'B')"
    )
    conn.execute(
        "INSERT INTO games(id, season, slate_date, tipoff, home_id, away_id, home_score,"
        " away_score, status, updated_at) VALUES('g1', '2026-27', '2026-11-13',"
        " '2026-11-14T00:00:00+00:00', 1, 2, 110, 100, 'STATUS_FINAL', 'x')"
    )
    conn.commit()
    return conn


def _edge(side="home", bettable=True, price=1.95):
    return {
        "market": "spread",
        "side": side,
        "line": -5.5,
        "price": price,
        "price_consensus": 1.92,
        "book": "draftkings",
        "model_prob": 0.56,
        "fair_prob": 0.51,
        "ev": 0.09,
        "edge": 0.05,
        "signal": True,
        "bettable": bettable,
    }


def test_log_signals_records_probabilities_and_prices():
    conn = _conn()
    n = log_signals(conn, "g1", "2026-11-13", [_edge()], "2026-11-13T12:00:00+00:00")
    assert n == 1
    row = dict(conn.execute("SELECT * FROM signal_log").fetchone())
    assert row["p_model"] == 0.56
    assert row["p_fair"] == 0.51
    assert row["p_calibrated"] is None
    assert row["p_implied"] == 1.0 / 1.95
    assert row["price_best"] == 1.95
    assert row["price_consensus"] == 1.92
    assert row["bettable"] == 1
    conn.close()


def test_log_signals_is_idempotent_per_state():
    conn = _conn()
    ts = "2026-11-13T12:00:00+00:00"
    assert log_signals(conn, "g1", "2026-11-13", [_edge()], ts) == 1
    # 同一 (game, market, side, bettable) 再次出现不重复记
    assert log_signals(conn, "g1", "2026-11-13", [_edge()], ts) == 0
    # 信号（不可下注）是另一个状态，可以单独记一行
    assert log_signals(conn, "g1", "2026-11-13", [_edge(bettable=False)], ts) == 1
    assert conn.execute("SELECT COUNT(*) FROM signal_log").fetchone()[0] == 2
    conn.close()


def test_settle_signal_log_fills_result_and_closing():
    conn = _conn()
    ts = "2026-11-13T12:00:00+00:00"
    log_signals(conn, "g1", "2026-11-13", [_edge(side="home", price=1.95)], ts)
    # 收盘快照：主队 -5.5 @1.90（开赛前）
    conn.execute(
        "INSERT INTO odds_snapshots(game_id, book, market, side, line, price, captured_at,"
        " last_seen_at, source) VALUES('g1', 'draftkings', 'spread', 'home', -5.5, 1.90,"
        " '2026-11-13T23:30:00+00:00', '2026-11-13T23:30:00+00:00', 'espn')"
    )
    conn.commit()
    updated = settle_signal_log(conn)
    assert updated == 1
    row = dict(conn.execute("SELECT * FROM signal_log").fetchone())
    assert row["result"] == "won"  # 主队 -5.5，赢 10 分
    assert row["profit"] == 0.95   # 1u × (1.95 − 1)
    assert row["price_closing"] == 1.90
    assert row["closing_line"] == -5.5
    # 幂等：已结算的行不再重复处理
    assert settle_signal_log(conn) == 0
    conn.close()
