# -*- coding: utf-8 -*-
"""盲区闸门（0.99）：impact=0 的未评分缺阵不再全盲。

背景：2026-11-21 LAC@DAL——Beal/Ingram/Miller 三人 out 但影响分全 0
（RAPM 无建议），既有闸门 `if impact <= 0: continue` 对它们全盲，
模型按满员计算推出幻影 edge。本文件验证两档拦截：
- 打分失败（本赛季有出场痕迹但 RAPM 无建议）≥1 个即拦；
- 名字无法解析（players 表无行）≥2 个才拦。
"""
from __future__ import annotations

import sqlite3

import pytest

from app.jobs import injury


@pytest.fixture()
def conn():
    """内存库：injuries/games/teams/players 四表，与生产列一致。"""
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
    c.execute("CREATE TABLE games (id TEXT PRIMARY KEY, home_id INTEGER, away_id INTEGER)")
    c.execute("INSERT INTO games(id, home_id, away_id) VALUES('g1', 5, 6)")
    c.execute("CREATE TABLE teams (id INTEGER PRIMARY KEY, abbrev TEXT)")
    c.execute("INSERT INTO teams(id, abbrev) VALUES(5, 'CLE')")
    c.execute("INSERT INTO teams(id, abbrev) VALUES(6, 'DAL')")
    # players 表：PBP 出场痕迹（Ingram 打过球，Beal/Miller 没打过）
    c.execute("CREATE TABLE players (id INTEGER PRIMARY KEY, name TEXT, team_id INTEGER)")
    c.execute("INSERT INTO players(id, name, team_id) VALUES(1, 'Brandon Ingram', 6)")
    yield c


def _ins(conn, team_id, player, status, impact=0.0, source="auto"):
    conn.execute(
        "INSERT INTO injuries(game_id, team_id, player, status, impact, captured_at, impact_source)"
        " VALUES('g1', ?, ?, ?, ?, '2026-11-21T18:00:00+00:00', ?)",
        (team_id, player, status, impact, source),
    )
    conn.commit()


def _edge(market, side, **kw):
    base = {
        "market": market, "side": side, "line": None, "price": 2.0,
        "bettable": True, "bettable_note": None, "gated": None, "signal": True,
    }
    base.update(kw)
    return base


def test_played_but_unscored_gates(conn, monkeypatch):
    """有出场痕迹的球员 out 且无建议（打分失败）→ 1 个即拦。"""
    _ins(conn, 6, "Brandon Ingram", "out")  # players 表有行
    monkeypatch.setattr(injury, "_suggestions", lambda conn, game_id, model=None: [])
    edges = [_edge("moneyline", "away")]
    out = injury.apply_injury_gate(conn, "g1", edges)
    assert out[0]["bettable"] is False
    note = out[0]["bettable_note"] or ""
    assert "盲区" in note and "Brandon Ingram" in note


def test_suggestion_with_zero_impact_not_gated(conn, monkeypatch):
    """RAPM 有建议（负评分钳到 0）= 打分流程正常，不是盲区。"""
    _ins(conn, 6, "Bench Guy", "out")
    monkeypatch.setattr(injury, "_suggestions", lambda conn, game_id, model=None: [
        {"team_id": 6, "player": "Bench Guy", "status": "out", "player_id": 9,
         "suggested_impact": 0.0, "manual_impact": 0.0},
    ])
    edges = [_edge("moneyline", "away")]
    out = injury.apply_injury_gate(conn, "g1", edges)
    assert out[0]["bettable"] is True


def test_unresolved_needs_two(conn, monkeypatch):
    """名字解析不到：1 个不拦（可能是纯板凳），2 个拦。"""
    _ins(conn, 6, "Unknown A", "out")
    monkeypatch.setattr(injury, "_suggestions", lambda conn, game_id, model=None: [])
    out = injury.apply_injury_gate(conn, "g1", [_edge("moneyline", "away")])
    assert out[0]["bettable"] is True

    _ins(conn, 6, "Unknown B", "out")
    out2 = injury.apply_injury_gate(conn, "g1", [_edge("moneyline", "away")])
    assert out2[0]["bettable"] is False
    assert "无法核实" in (out2[0]["bettable_note"] or "")


def test_never_played_single_not_gated(conn, monkeypatch):
    """确认赛季未出场（players 表无行且无同名）1 个 → 不拦（与测试同款单 unknown）。"""
    _ins(conn, 5, "Some Nobody", "out")
    monkeypatch.setattr(injury, "_suggestions", lambda conn, game_id, model=None: [])
    out = injury.apply_injury_gate(conn, "g1", [_edge("moneyline", "home")])
    assert out[0]["bettable"] is True


def test_manual_zero_impact_not_gated(conn, monkeypatch):
    """manual 手设 0 分 = 人工确认无影响，不进盲区。"""
    _ins(conn, 6, "Brandon Ingram", "out", impact=0.0, source="manual")
    monkeypatch.setattr(injury, "_suggestions", lambda conn, game_id, model=None: [])
    out = injury.apply_injury_gate(conn, "g1", [_edge("moneyline", "away")])
    assert out[0]["bettable"] is True


def test_positive_impact_still_gated(conn, monkeypatch):
    """老分值闸门不回归：正分 ≥3 照旧拦（且不重复标注盲区）。"""
    _ins(conn, 5, "Star Man", "out", impact=3.5)
    monkeypatch.setattr(injury, "_suggestions", lambda conn, game_id, model=None: [
        {"team_id": 5, "player": "Star Man", "status": "out", "player_id": 3,
         "suggested_impact": 3.5, "manual_impact": 0.0},
    ])
    out = injury.apply_injury_gate(conn, "g1", [_edge("moneyline", "home")])
    assert out[0]["bettable"] is False
    note = out[0]["bettable_note"] or ""
    assert "伤停闸门" in note and "盲区" not in note


def test_total_market_gated_by_either_side(conn, monkeypatch):
    """大小分：任一侧触发盲区即拦（方向不可知，保守双杀）。"""
    _ins(conn, 6, "Brandon Ingram", "out")
    monkeypatch.setattr(injury, "_suggestions", lambda conn, game_id, model=None: [])
    out = injury.apply_injury_gate(conn, "g1", [_edge("total", "over"), _edge("total", "under")])
    assert out[0]["bettable"] is False and out[1]["bettable"] is False


def test_scored_injury_masks_same_player(conn, monkeypatch):
    """同球员既有正分建议就正常走老闸门，不进盲区桶。"""
    _ins(conn, 6, "Brandon Ingram", "out", impact=4.0)
    monkeypatch.setattr(injury, "_suggestions", lambda conn, game_id, model=None: [
        {"team_id": 6, "player": "Brandon Ingram", "status": "out", "player_id": 1,
         "suggested_impact": 4.0, "manual_impact": 0.0},
    ])
    out = injury.apply_injury_gate(conn, "g1", [_edge("moneyline", "away")])
    assert out[0]["bettable"] is False
    assert "盲区" not in (out[0]["bettable_note"] or "")
