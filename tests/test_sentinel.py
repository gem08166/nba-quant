# -*- coding: utf-8 -*-
"""夜间哨兵数据通道：导出包结构 + 合并幂等性（不碰生产库、不发推送）。"""
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.jobs import sentinel


def _fresh_db(tmp_path):
    path = tmp_path / "sentinel.db"
    conn = db.connect(path)
    db.init_db(conn)
    conn.close()
    # 第二次 connect 会跑迁移（补 live_home_score 等列）
    return db.connect(path)


def _payload():
    now = datetime(2026, 11, 21, 6, 0, tzinfo=timezone.utc).isoformat(timespec="seconds")
    return {
        "slate": "2026-11-20",
        "exported_at": now,
        "games": [
            {
                "id": "g1", "season": "2026", "season_type": 2, "slate_date": "2026-11-20",
                "tipoff": "2026-11-21T00:00Z", "home_id": 1, "away_id": 2,
                "home_score": None, "away_score": None, "status": "STATUS_SCHEDULED",
                "status_detail": "", "venue": "Arena", "updated_at": now,
                "live_home_score": None, "live_away_score": None,
            }
        ],
        "odds": [
            # moneyline 的 line 为 NULL：SQLite UNIQUE 对 NULL 不去重，必须靠键比对
            {"game_id": "g1", "book": "DraftKings", "market": "moneyline", "side": "home",
             "line": None, "price": 1.42, "captured_at": now, "source": "espn", "last_seen_at": now},
            {"game_id": "g1", "book": "DraftKings", "market": "spread", "side": "home",
             "line": -6.5, "price": 1.91, "captured_at": now, "source": "espn", "last_seen_at": now},
        ],
        "injuries": [
            {"game_id": "g1", "team_id": 2, "player": "Test Player", "status": "out",
             "detail": "Ankle", "impact": 0.0, "captured_at": now, "impact_source": "auto",
             "applied_status": None, "applied_at": None},
        ],
    }


def test_merge_is_idempotent(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        payload = _payload()
        first = sentinel._merge(conn, payload)
        conn.commit()
        assert first == {"games_new": 1, "games_updated": 0, "odds_new": 2, "injuries_new": 1}

        second = sentinel._merge(conn, payload)
        conn.commit()
        assert second == {"games_new": 0, "games_updated": 0, "odds_new": 0, "injuries_new": 0}

        assert conn.execute("select count(*) from odds_snapshots").fetchone()[0] == 2
    finally:
        conn.close()


def test_merge_prefers_newer_game_state(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        payload = _payload()
        sentinel._merge(conn, payload)
        conn.commit()

        newer = _payload()
        newer["games"][0]["status"] = "STATUS_FINAL"
        newer["games"][0]["home_score"] = 112
        newer["games"][0]["updated_at"] = "2026-11-21T07:00:00+00:00"
        stats = sentinel._merge(conn, newer)
        conn.commit()
        assert stats["games_updated"] == 1
        row = conn.execute("select status, home_score from games where id='g1'").fetchone()
        assert row["status"] == "STATUS_FINAL" and row["home_score"] == 112

        older = _payload()  # 旧 updated_at 不应回退
        stats2 = sentinel._merge(conn, older)
        conn.commit()
        assert stats2["games_updated"] == 0
    finally:
        conn.close()


def test_preflight_warns_on_empty_history(tmp_path):
    """空库（无完赛历史）必须显式告警：否则 predict 每轮失败、整夜 0 推送。"""
    from scripts import sentinel as sentinel_script

    conn = _fresh_db(tmp_path)
    messages: list[str] = []
    original = sentinel_script._log
    sentinel_script._log = messages.append
    try:
        finals = sentinel_script._preflight(conn)
    finally:
        sentinel_script._log = original
        conn.close()
    assert finals == 0
    assert any("无法训练" in m for m in messages)


def test_preflight_ok_with_enough_finals(tmp_path):
    from scripts import sentinel as sentinel_script

    conn = _fresh_db(tmp_path)
    rows = [
        (f"g{i}", "2026", 2, "2026-11-01", "2026-11-02T00:00Z", 1, 2,
         "STATUS_FINAL", 100, 99, "2026-11-02T03:00:00+00:00")
        for i in range(30)
    ]
    conn.executemany(
        "insert into games (id,season,season_type,slate_date,tipoff,home_id,away_id,"
        "status,home_score,away_score,updated_at) values (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    messages: list[str] = []
    original = sentinel_script._log
    sentinel_script._log = messages.append
    try:
        finals = sentinel_script._preflight(conn)
    finally:
        sentinel_script._log = original
        conn.close()
    assert finals == 30
    assert any("preflight ok" in m for m in messages)


def test_build_payload_scopes_to_slate_window(tmp_path):
    conn = _fresh_db(tmp_path)
    try:
        conn.execute(
            "insert into games (id,season,season_type,slate_date,tipoff,home_id,away_id,status,updated_at) "
            "values ('g_in','2026',2,'2026-11-20','2026-11-21T00:00Z',1,2,'STATUS_SCHEDULED','2026-11-21T06:00:00+00:00')"
        )
        conn.execute(
            "insert into games (id,season,season_type,slate_date,tipoff,home_id,away_id,status,updated_at) "
            "values ('g_out','2026',2,'2026-11-10','2026-11-11T00:00Z',1,2,'STATUS_FINAL','2026-11-11T06:00:00+00:00')"
        )
        conn.commit()
        payload = sentinel.build_payload(conn, "2026-11-20")
        ids = {g["id"] for g in payload["games"]}
        assert ids == {"g_in"}
    finally:
        conn.close()
