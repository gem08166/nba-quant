"""betssync（台账 ↔ 坚果云）合并逻辑的单元测试：不联网，用最小 schema。"""
import sqlite3

from app.jobs import betssync


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE bets (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          game_id TEXT NOT NULL, market TEXT NOT NULL, side TEXT NOT NULL,
          line REAL, price REAL, stake REAL DEFAULT 1.0,
          model_prob REAL, ev REAL, placed_at TEXT,
          result TEXT DEFAULT 'open', profit REAL,
          closing_line REAL, closing_price REAL, clv REAL, clv_line REAL,
          note TEXT, ref TEXT
        );
        CREATE UNIQUE INDEX idx_bets_ref ON bets(ref) WHERE ref IS NOT NULL;
        """
    )
    return conn


def _row(ref_id: str, result: str = "open") -> dict:
    return {
        "id": ref_id,
        "game_id": "g1",
        "market": "spread",
        "side": "home",
        "line": -2.5,
        "price": 1.97,
        "stake": 1.0,
        "placed_at": "2026-11-04T13:00:00+00:00",
        "result": result,
        "profit": None if result == "open" else 0.97,
    }


def test_merge_dedups_by_ref():
    conn = _conn()
    first = betssync.merge_rows(conn, [_row("local-abc"), _row("local-abc")])
    assert first["added"] == 1
    assert first["duplicates"] == 1
    assert first["skipped"] == 1
    again = betssync.merge_rows(conn, [_row("local-abc")])
    assert again["added"] == 0
    assert again["duplicates"] == 1
    assert conn.execute("SELECT COUNT(*) n FROM bets").fetchone()["n"] == 1


def test_merge_generates_ref_when_missing():
    conn = _conn()
    row = _row("")
    row.pop("id")
    result = betssync.merge_rows(conn, [row, dict(row)])
    assert result["added"] == 1
    ref = conn.execute("SELECT ref FROM bets").fetchone()["ref"]
    assert ref.startswith("gen-")


def test_settled_version_overwrites_open():
    """云端已结算的版本要能补齐本地 open（单调更新），反向不允许降级。"""
    conn = _conn()
    r1 = betssync.merge_rows(conn, [_row("local-abc", result="open")])
    assert r1["added"] == 1

    r2 = betssync.merge_rows(conn, [_row("local-abc", result="won")])
    assert r2["added"] == 0 and r2["updated"] == 1
    row = conn.execute("SELECT result, profit FROM bets WHERE ref='local-abc'").fetchone()
    assert row["result"] == "won"
    assert row["profit"] == 0.97

    # 已结算的注单不能被后来的 open 版本改回去
    r3 = betssync.merge_rows(conn, [_row("local-abc", result="open")])
    assert r3["updated"] == 0
    assert (
        conn.execute("SELECT result FROM bets WHERE ref='local-abc'").fetchone()["result"]
        == "won"
    )


def test_bad_rows_are_skipped_not_fatal():
    conn = _conn()
    result = betssync.merge_rows(conn, [{"bad": 1}, None, _row("local-xyz")])
    assert result["added"] == 1
    assert conn.execute("SELECT COUNT(*) n FROM bets").fetchone()["n"] == 1
