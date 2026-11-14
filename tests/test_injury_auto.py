"""伤停自动采纳 / 闸门 / 快照（0.87）。

测试用内存库自建 injuries 表（列与生产一致 + 迁移新列），
不依赖 PBP 名册：建议值统一 monkeypatch `injury._suggestions`。
"""
from __future__ import annotations

import json
import sqlite3

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


def test_snapshot_shape(conn):
    _ins(conn, "g1", 5, "Evan Mobley", "out", "2026-11-14T18:00:00+00:00", 2.5, source="manual")
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


def test_migrate_adds_injury_columns(tmp_path):
    """迁移：旧库（无新列）打开后补齐 3 列，幂等。"""
    import shutil

    from app import db as dbmod

    work = tmp_path / "nba.db"
    shutil.copy("data/nba.db", work)
    conn = dbmod.connect(work)  # connect() 内部跑 _migrate
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(injuries)")}
        assert {"impact_source", "applied_status", "applied_at"} <= cols
        cols2 = {r["name"] for r in conn.execute("PRAGMA table_info(signal_log)")}
        assert "injuries" in cols2
    finally:
        conn.close()
