"""--bettable-only 口径下 _select_signals 的行为。"""
import json
import sqlite3

from scripts.backfill_bets import _select_signals


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE predictions (id INTEGER PRIMARY KEY, game_id TEXT, created_at TEXT, details TEXT);
        """
    )
    return conn


def _row(conn, game, created, edges):
    conn.execute(
        "INSERT INTO predictions(game_id, created_at, details) VALUES(?,?,?)",
        (game, created, json.dumps({"edges": edges})),
    )


def _edge(market, side, line, ev=0.1, bettable=None, price=1.95):
    e = {"market": market, "side": side, "line": line, "signal": True, "ev": ev, "price": price}
    if bettable is not None:
        e["bettable"] = bettable
        e["bettable_note"] = "" if bettable else "阀门拦截"
    return e


def rows_of(conn):
    return conn.execute("SELECT game_id, created_at, details FROM predictions ORDER BY id").fetchall()


def test_bettable_only_excludes_missing_field_and_false():
    conn = _conn()
    _row(conn, "g1", "2026-11-03T09:00:00Z", [_edge("moneyline", "home", None)])      # 旧 schema 无字段
    _row(conn, "g2", "2026-11-05T09:00:00Z", [_edge("moneyline", "home", None, bettable=False)])
    _row(conn, "g3", "2026-11-05T09:00:00Z", [_edge("moneyline", "home", None, bettable=True)])
    _row(conn, "g4", "2026-11-05T09:00:00Z", [
        _edge("moneyline", "home", None, ev=0.2, bettable=False),
        _edge("total", "over", 220.0, bettable=True),
    ])

    items = _select_signals(rows_of(conn), strict=True, bettable_only=True)
    games = sorted({i["row"]["game_id"] for i in items})
    assert games == ["g3", "g4"]
    # g4 的 moneyline（bettable=False）被排除，total 保留
    markets = {i["edge"]["market"] for i in items if i["row"]["game_id"] == "g4"}
    assert markets == {"total"}


def test_bettable_only_false_keeps_all_signals():
    conn = _conn()
    _row(conn, "g1", "2026-11-03T09:00:00Z", [_edge("moneyline", "home", None)])  # 旧 schema
    _row(conn, "g2", "2026-11-05T09:00:00Z", [_edge("moneyline", "home", None, bettable=False)])
    items = _select_signals(rows_of(conn), strict=True, bettable_only=False)
    assert len(items) == 2


def test_bettable_only_first_passing_version_wins():
    conn = _conn()
    # 同一信号：先 bettable=False，后 bettable=True（价格改善后阀门放行）
    _row(conn, "g1", "2026-11-05T08:00:00Z", [_edge("moneyline", "home", None, bettable=False, price=1.80)])
    _row(conn, "g1", "2026-11-05T10:00:00Z", [_edge("moneyline", "home", None, bettable=True, price=1.90)])
    items = _select_signals(rows_of(conn), strict=False, bettable_only=True)
    assert len(items) == 1
    assert items[0]["edge"]["price"] == 1.90
    assert items[0]["row"]["created_at"] == "2026-11-05T10:00:00Z"
