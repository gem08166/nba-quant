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
