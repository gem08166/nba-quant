"""真实注单去重：手机/PC 各记一次（ref 不同、时间差几分钟）只落一条。"""
import sqlite3

from app.jobs import betssync


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE bets (
            id INTEGER PRIMARY KEY, game_id TEXT, market TEXT, side TEXT, line REAL,
            price REAL, stake REAL, model_prob REAL, ev REAL, placed_at TEXT,
            result TEXT, profit REAL, closing_line REAL, closing_price REAL,
            clv REAL, clv_line REAL, note TEXT, ref TEXT
        );
        CREATE UNIQUE INDEX idx_bets_ref ON bets(ref) WHERE ref IS NOT NULL;
        """
    )
    return conn


def _bet(ref, placed, price=1.95, stake=2.0, note="lowvig.ag / model 55%", result="open"):
    return {
        "game_id": "g1",
        "market": "spread",
        "side": "home",
        "line": -2.5,
        "price": price,
        "stake": stake,
        "model_prob": 0.55,
        "placed_at": placed,
        "note": note,
        "result": result,
        "ref": ref,
    }


def test_same_bet_from_two_devices_is_deduped():
    conn = _conn()
    betssync.merge_rows(conn, [_bet("local-a", "2026-11-06T16:03:03.906Z", price=1.88, stake=1.0)])
    # 11 分钟后从 PC 又记了一次，赔率一致、ref 不同、时间不同
    result = betssync.merge_rows(conn, [_bet("srv-9", "2026-11-06T16:14:29+00:00", price=1.88, stake=5.0)])
    assert result["added"] == 0
    assert result["duplicates"] == 1
    assert conn.execute("SELECT COUNT(*) n FROM bets").fetchone()["n"] == 1


def test_different_price_is_not_deduped():
    conn = _conn()
    betssync.merge_rows(conn, [_bet("local-a", "2026-11-06T16:03:00+00:00", price=1.87, stake=2.0)])
    result = betssync.merge_rows(conn, [_bet("srv-9", "2026-11-06T16:14:00+00:00", price=1.98, stake=2.0)])
    assert result["added"] == 1
    assert conn.execute("SELECT COUNT(*) n FROM bets").fetchone()["n"] == 2


def test_far_apart_in_time_is_not_deduped():
    conn = _conn()
    betssync.merge_rows(conn, [_bet("local-a", "2026-11-06T08:00:00+00:00", price=1.90, stake=2.0)])
    result = betssync.merge_rows(conn, [_bet("srv-9", "2026-11-07T20:00:00+00:00", price=1.90, stake=2.0)])
    assert result["added"] == 1


def test_sim_and_real_do_not_collapse():
    conn = _conn()
    betssync.merge_rows(conn, [_bet("local-a", "2026-11-06T16:03:00+00:00", note="lowvig.ag / model")])
    # 模拟单哪怕同盘口同价，也必须单独保留（两个分组各自统计）
    result = betssync.merge_rows(
        conn,
        [_bet("sim-abc", "2026-11-06T16:04:00+00:00", note="模拟 可下注 · lowvig.ag")],
    )
    assert result["added"] == 1
    assert conn.execute("SELECT COUNT(*) n FROM bets").fetchone()["n"] == 2
