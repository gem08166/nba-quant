"""伤停自动采纳 / 闸门 / 快照（0.87）。

测试用内存库自建 injuries 表（列与生产一致 + 迁移新列），
不依赖 PBP 名册：建议值统一 monkeypatch `injury._suggestions`。
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.jobs import injury


@pytest.fixture()
def conn():
    """内存库 + 与生产一致的 injuries 表（含迁移新列）。"""
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    c.execute(
        """
        CREATE TABLE injuries (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id     TEXT NOT NULL,
            team_id     INTEGER NOT NULL,
            player      TEXT NOT NULL,
            status      TEXT,
            detail      TEXT,
            impact      REAL NOT NULL DEFAULT 0,
            captured_at TEXT NOT NULL,
            impact_source TEXT NOT NULL DEFAULT 'auto',
            applied_status TEXT,
            applied_at  TEXT,
            UNIQUE(game_id, team_id, player, status)
        )
        """
    )
    c.execute(
        """
        CREATE TABLE games (
            id TEXT PRIMARY KEY,
            home_id INTEGER,
            away_id INTEGER
        )
        """
    )
    c.execute("INSERT INTO games(id, home_id, away_id) VALUES('g1', 5, 6)")
    c.execute(
        """
        CREATE TABLE teams (
            id INTEGER PRIMARY KEY,
            abbrev TEXT
        )
        """
    )
    c.execute("INSERT INTO teams(id, abbrev) VALUES(5, 'CLE')")
    c.execute("INSERT INTO teams(id, abbrev) VALUES(6, 'DAL')")
    yield c
    c.close()


def _ins(conn, game_id, team_id, player, status, captured_at, impact=0.0, source="auto"):
    conn.execute(
        "INSERT INTO injuries(game_id, team_id, player, status, impact, captured_at, impact_source)"
        " VALUES(?,?,?,?,?,?,?)",
        (game_id, team_id, player, status, impact, captured_at, source),
    )


# ---------- Task 1: 去重读取 ----------


def test_game_injuries_dedup_takes_latest_row(conn):
    """同一球员 day-to-day(15:00) + out(18:00) 并存 → 只取 out 行。"""
    _ins(conn, "g1", 5, "Evan Mobley", "day-to-day", "2026-11-14T15:00:00+00:00", 0.0)
    _ins(conn, "g1", 5, "Evan Mobley", "out", "2026-11-14T18:00:00+00:00", 2.5)
    _ins(conn, "g1", 6, "Other Guy", "out", "2026-11-14T17:00:00+00:00", 1.0)

    rows = injury.game_injuries(conn, "g1")
    mobley = [r for r in rows if r["player"] == "Evan Mobley"]
    assert len(mobley) == 1
    assert mobley[0]["status"] == "out"
    assert mobley[0]["impact"] == 2.5
    assert len(rows) == 2  # 去重后两人


def test_snapshot_shape(conn, monkeypatch):
    _ins(conn, "g1", 5, "Evan Mobley", "out", "2026-11-14T18:00:00+00:00", 2.5, source="manual")
    _mock_suggestions(monkeypatch, [])  # manual 行不查建议
    snap = injury.snapshot(conn, "g1")
    assert snap == [
        {"team_id": 5, "player": "Evan Mobley", "status": "out", "impact": 2.5, "impact_source": "manual"}
    ]


# ---------- Task 2: 自动采纳 ----------


def _mock_suggestions(monkeypatch, rows):
    """固定建议列表，避免测试依赖 PBP 名册。"""
    monkeypatch.setattr(injury, "_suggestions", lambda conn, gid, model=None: list(rows))


def test_apply_impacts_writes_suggestion(conn, monkeypatch):
    _ins(conn, "g1", 5, "Evan Mobley", "out", "2026-11-14T18:00:00+00:00")
    _mock_suggestions(monkeypatch, [
        {"team_id": 5, "player": "Evan Mobley", "status": "out", "player_id": 42,
         "suggested_impact": 3.2, "manual_impact": 0.0},
    ])
    stats = injury.apply_impacts(conn, ["g1"])
    assert stats["applied"] == 1
    row = dict(conn.execute("SELECT * FROM injuries WHERE player='Evan Mobley'").fetchone())
    assert row["impact"] == 3.2
    assert row["impact_source"] == "auto"
    assert row["applied_status"] == "out"
    assert row["applied_at"]


def test_apply_impacts_respects_manual(conn, monkeypatch):
    _ins(conn, "g1", 5, "Evan Mobley", "out", "2026-11-14T18:00:00+00:00", 1.0, source="manual")
    _mock_suggestions(monkeypatch, [
        {"team_id": 5, "player": "Evan Mobley", "status": "out", "player_id": 42,
         "suggested_impact": 3.2, "manual_impact": 1.0},
    ])
    stats = injury.apply_impacts(conn, ["g1"])
    assert stats["skipped"] == 1
    row = dict(conn.execute("SELECT * FROM injuries WHERE player='Evan Mobley'").fetchone())
    assert row["impact"] == 1.0  # 手设值不动
    assert row["applied_at"] is None


def test_apply_impacts_zeroes_when_no_suggestion(conn, monkeypatch):
    """状态翻出因子表 / 匹配不到球员 → 无建议 → impact 归 0（复活）。"""
    _ins(conn, "g1", 5, "Evan Mobley", "out", "2026-11-14T18:00:00+00:00", 3.2)
    _ins(conn, "g1", 5, "Evan Mobley", "day-to-day", "2026-11-14T20:00:00+00:00")  # 最新行
    _mock_suggestions(monkeypatch, [])  # day-to-day 行匹配不到 PBP id → 无建议
    stats = injury.apply_impacts(conn, ["g1"])
    assert stats["zeroed"] == 1
    # 最新行（day-to-day@20:00）的 impact 应被归零
    row = dict(
        conn.execute(
            "SELECT * FROM injuries WHERE player='Evan Mobley' ORDER BY captured_at DESC LIMIT 1"
        ).fetchone()
    )
    assert row["impact"] == 0.0
    assert row["applied_status"] == "day-to-day"
    # 旧行（out@18:00）已被去重逻辑排除，不被动过
    old = dict(
        conn.execute(
            "SELECT * FROM injuries WHERE player='Evan Mobley' AND status='out'"
        ).fetchone()
    )
    assert old["impact"] == 3.2
    assert old["applied_at"] is None


# ---------- Task 3: 伤停闸门 ----------


def _edge(market, side, **kw):
    base = {
        "market": market, "side": side, "line": None, "price": 2.0,
        "bettable": True, "bettable_note": None, "gated": None, "signal": True,
    }
    base.update(kw)
    return base


def test_gate_degrades_home_ml_when_home_out_heavy(conn, monkeypatch):
    _ins(conn, "g1", 5, "Evan Mobley", "out", "2026-11-14T18:00:00+00:00", 3.2)
    _mock_suggestions(monkeypatch, [
        {"team_id": 5, "player": "Evan Mobley", "status": "out", "player_id": 42,
         "suggested_impact": 3.2, "manual_impact": 0.0},
    ])
    edges = [_edge("moneyline", "home"), _edge("moneyline", "away", bettable=False)]
    out = injury.apply_injury_gate(conn, "g1", edges)
    assert out[0]["bettable"] is False
    assert "伤停闸门" in (out[0]["bettable_note"] or "")
    assert "Evan Mobley" in (out[0]["bettable_note"] or "")
    assert out[0]["gated"] == out[0]["bettable_note"]
    assert out[1]["bettable"] is False  # 本来就不是 bettable 的不碰


def test_gate_ignores_away_side_injury_for_home_bet(conn, monkeypatch):
    """away 侧缺人不影响 home 方向的信号。"""
    _ins(conn, "g1", 6, "Nikola Jokic", "out", "2026-11-14T18:00:00+00:00", 5.0)
    _mock_suggestions(monkeypatch, [
        {"team_id": 6, "player": "Nikola Jokic", "status": "out", "player_id": 7,
         "suggested_impact": 5.0, "manual_impact": 0.0},
    ])
    edges = [_edge("spread", "home", line=12.5)]
    out = injury.apply_injury_gate(conn, "g1", edges)
    assert out[0]["bettable"] is True


def test_gate_skips_total_market(conn, monkeypatch):
    _ins(conn, "g1", 5, "Evan Mobley", "out", "2026-11-14T18:00:00+00:00", 3.2)
    _mock_suggestions(monkeypatch, [
        {"team_id": 5, "player": "Evan Mobley", "status": "out", "player_id": 42,
         "suggested_impact": 3.2, "manual_impact": 0.0},
    ])
    edges = [_edge("total", "over", line=231.5)]
    out = injury.apply_injury_gate(conn, "g1", edges)
    assert out[0]["bettable"] is True


def test_gate_below_threshold_no_degrade(conn, monkeypatch):
    """合计分值低于阈值（默认 3.0）不降级。"""
    _ins(conn, "g1", 5, "Role Player", "out", "2026-11-14T18:00:00+00:00", 1.5)
    _mock_suggestions(monkeypatch, [
        {"team_id": 5, "player": "Role Player", "status": "out", "player_id": 9,
         "suggested_impact": 1.5, "manual_impact": 0.0},
    ])
    edges = [_edge("moneyline", "home")]
    out = injury.apply_injury_gate(conn, "g1", edges)
    assert out[0]["bettable"] is True


def test_gate_disabled_flag(conn, monkeypatch):
    _ins(conn, "g1", 5, "Evan Mobley", "out", "2026-11-14T18:00:00+00:00", 3.2)
    _mock_suggestions(monkeypatch, [
        {"team_id": 5, "player": "Evan Mobley", "status": "out", "player_id": 42,
         "suggested_impact": 3.2, "manual_impact": 0.0},
    ])
    # injury.py 模块级绑定了旗标，patch injury 自己的名字才生效
    monkeypatch.setattr(injury, "INJURY_GATE_ENABLE", False)
    edges = [_edge("moneyline", "home")]
    out = injury.apply_injury_gate(conn, "g1", edges)
    assert out[0]["bettable"] is True


def test_side_of_mapping():
    assert injury.side_of({"side": "h"}) == "home"
    assert injury.side_of({"side": "a"}) == "away"
    assert injury.side_of({"side": "ov"}) is None
    assert injury.side_of({"side": "ud"}) is None


# ---------- Task 4: signal_log 伤停快照 ----------


@pytest.fixture()
def signal_conn(conn):
    """加 signal_log 表（与生产同构，含 injuries 列）。"""
    conn.execute(
        """
        CREATE TABLE signal_log (
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
            injuries        TEXT,
            UNIQUE(game_id, market, side, bettable)
        )
        """
    )
    return conn


def test_log_signals_writes_injury_snapshot(signal_conn, monkeypatch):
    from app.jobs.predict import log_signals

    # impact=3.2 但 source=auto 且无建议 → 快照按决策口径记建议值 0.0；
    # 手设值（manual）才会被尊重。断言随 F5 语义更新。
    _ins(signal_conn, "g1", 5, "Evan Mobley", "out", "2026-11-14T18:00:00+00:00", 3.2)
    _mock_suggestions(monkeypatch, [])
    snap = injury.snapshot(signal_conn, "g1")
    assert snap[0]["impact"] == 0.0  # auto 行无建议 → 决策口径 0

    _ins(signal_conn, "g1", 5, "Manual Guy", "out", "2026-11-14T18:00:00+00:00", 4.0, source="manual")
    snap = injury.snapshot(signal_conn, "g1")
    manual = [x for x in snap if x["player"] == "Manual Guy"][0]
    assert manual["impact"] == 4.0
    edges = [_edge("moneyline", "home", model_prob=0.55, ev=0.1, edge=0.05)]
    n = log_signals(
        signal_conn, "g1", "2026-11-14", edges, "2026-11-14T20:00:00+00:00",
        injury_snapshot=snap,
    )
    assert n == 1
    row = dict(signal_conn.execute("SELECT * FROM signal_log WHERE game_id='g1'").fetchone())
    payload = json.loads(row["injuries"])
    assert payload and payload[0]["player"] == "Evan Mobley"
    assert payload[0]["status"] == "out"


def test_log_signals_without_snapshot_leaves_null(signal_conn):
    from app.jobs.predict import log_signals

    edges = [_edge("moneyline", "home", model_prob=0.55, ev=0.1, edge=0.05)]
    log_signals(signal_conn, "g2", "2026-11-14", edges, "2026-11-14T20:00:00+00:00")
    row = dict(signal_conn.execute("SELECT * FROM signal_log WHERE game_id='g2'").fetchone())
    assert row["injuries"] is None


def test_migrate_adds_injury_columns(tmp_path):
    """迁移：旧库（无新列）打开后补齐 3 列，幂等。"""
    import shutil

    import pytest

    from app import db as dbmod

    src = Path("data/nba.db")
    if not src.exists():
        pytest.skip("本地无 data/nba.db（新 clone 未恢复 data 分支）")
    work = tmp_path / "nba.db"
    shutil.copy(src, work)
    conn = dbmod.connect(work)  # connect() 内部跑 _migrate
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(injuries)")}
        assert {"impact_source", "applied_status", "applied_at"} <= cols
        cols2 = {r["name"] for r in conn.execute("PRAGMA table_info(signal_log)")}
        assert "injuries" in cols2
    finally:
        conn.close()
