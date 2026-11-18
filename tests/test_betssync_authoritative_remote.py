"""bets-sync run()：authoritative_remote 只让 CI 走「台账 profit 覆盖本地」。"""
import json
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
        """
    )
    return conn


def _bet(ref, result, profit):
    return {
        "game_id": "g1",
        "market": "spread",
        "side": "home",
        "line": -2.5,
        "price": 1.95,
        "stake": 2.0,
        "model_prob": 0.5,
        "ev": 0.0,
        "placed_at": "2026-11-18T08:00:00+08:00",
        "result": result,
        "profit": profit,
        "ref": ref,
    }


class _Dav:
    def __init__(self, monkeypatch, ledger_rows):
        monkeypatch.setattr(betssync.webdav, "enabled", lambda: True)
        monkeypatch.setattr(betssync.webdav, "ensure_folders", lambda: None)
        monkeypatch.setattr(betssync.webdav, "list_files", lambda path: [])
        monkeypatch.setattr(
            betssync.webdav,
            "get_text",
            lambda path: json.dumps(ledger_rows) if path == betssync.webdav.LEDGER else None,
        )
        monkeypatch.setattr(betssync.webdav, "put_text", lambda path, body: True)


def _run(monkeypatch, authoritative):
    ledger = [_bet("hao-1", "lost", -1.6)]
    _Dav(monkeypatch, ledger)
    conn = _conn()
    conn.execute(
        """INSERT INTO bets(game_id, market, side, line, price, stake, model_prob, ev,
               placed_at, result, profit, note, ref)
           VALUES('g1','spread','home',-2.5,1.95,2.0,0.5,0.0,
                  '2026-11-18T08:00:00+08:00','lost',-2.0,'333体育','hao-1')"""
    )
    conn.commit()
    betssync.run(conn, pull_only=True, authoritative_remote=authoritative)
    return conn.execute("SELECT profit FROM bets WHERE ref='hao-1'").fetchone()["profit"]


def test_ci_pull_uses_ledger_profit(monkeypatch):
    """CI 恢复的库自带保守 -2.00 → 权威拉取后应被台账 -1.60 覆盖。"""
    assert _run(monkeypatch, True) == -1.6


def test_daemon_pull_keeps_local_profit(monkeypatch):
    """PC 守护进程的非权威拉取不得回吐本地已修正的 -1.60。"""
    ledger = [_bet("hao-1", "lost", -2.0)]
    _Dav(monkeypatch, ledger)
    conn = _conn()
    conn.execute(
        """INSERT INTO bets(game_id, market, side, line, price, stake, model_prob, ev,
               placed_at, result, profit, note, ref)
           VALUES('g1','spread','home',-2.5,1.95,2.0,0.5,0.0,
                  '2026-11-18T08:00:00+08:00','lost',-1.6,'333体育','hao-1')"""
    )
    conn.commit()
    betssync.run(conn, pull_only=True)
    assert conn.execute("SELECT profit FROM bets WHERE ref='hao-1'").fetchone()["profit"] == -1.6
