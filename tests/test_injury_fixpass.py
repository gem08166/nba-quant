# -*- coding: utf-8 -*-
"""最终审查修复 pass 的失败测试（F1-F5, M1）。

F1: predict_game 求和不去重 → 双计/幽灵伤停（复现：同球员两行，旧行 impact
    3.2 残留 + 新行 0 → 求和应为 0 而不是 3.2）
F2: 建议 key 冲突时 last-rowid-wins → 应与去重行对齐（用去重行的建议）
F3: api 两条手写路径不落 impact_source='manual' → manual 否决权不可达；
    且闸门应优先用手设值
F4: 闸门把 questionable/day-to-day 计入 → 规格说 out/doubtful 才拦
F5: snapshot 记录的是 0.0 → 应记录建议值（AUTO_APPLY 关时校准才有数据）
M1: note 用队名缩写而不是 home/away；死变量清理
"""
import json
import sqlite3

import pytest

from app.jobs import injury


@pytest.fixture()
def conn():
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
    c.execute("CREATE TABLE teams (id INTEGER PRIMARY KEY, abbrev TEXT)")
    c.execute("INSERT INTO games VALUES('g1', 5, 6)")
    c.execute("INSERT INTO teams VALUES(5, 'CLE')")
    c.execute("INSERT INTO teams VALUES(6, 'DAL')")
    yield c
    c.close()


def _ins(conn, game_id, team_id, player, status, captured_at, impact=0.0, source="auto"):
    conn.execute(
        "INSERT INTO injuries(game_id, team_id, player, status, impact, captured_at, impact_source)"
        " VALUES(?,?,?,?,?,?,?)",
        (game_id, team_id, player, status, impact, captured_at, source),
    )


def _sug(monkeypatch, rows):
    monkeypatch.setattr(injury, "_suggestions", lambda conn, gid, model=None: list(rows))


# ---------- F2: 建议与去重行对齐 ----------


def test_gate_uses_dedup_row_suggestion(conn, monkeypatch):
    """同一球员 out(旧行) + day-to-day(新行) 都返回建议时，
    闸门必须用**去重行（最新 captured_at）**对应的那条建议，而不是 rowid 顺序碰运气。"""
    _ins(conn, "g1", 5, "Star", "out", "2026-11-14T18:00:00+00:00", 0.0)  # 旧行 rowid 小
    _ins(conn, "g1", 5, "Star", "day-to-day", "2026-11-14T20:00:00+00:00", 0.0)  # 新行
    _sug(monkeypatch, [
        {"team_id": 5, "player": "Star", "status": "day-to-day", "suggested_impact": 1.6},
    ])
    edges = [{"market": "moneyline", "side": "home", "bettable": True, "bettable_note": None, "gated": None}]
    out = injury.apply_injury_gate(conn, "g1", edges)
    # day-to-day 建议 1.6 < 3.0 → 不拦
    assert out[0]["bettable"] is True


def test_apply_impacts_aligns_applied_status_with_dedup_row(conn, monkeypatch):
    """采纳时 applied_status 必须与去重行自身 status 一致（审计口径）。"""
    _ins(conn, "g1", 5, "Star", "out", "2026-11-14T18:00:00+00:00")
    _ins(conn, "g1", 5, "Star", "day-to-day", "2026-11-14T20:00:00+00:00")
    _sug(monkeypatch, [
        {"team_id": 5, "player": "Star", "status": "day-to-day", "suggested_impact": 1.6},
    ])
    injury.apply_impacts(conn, ["g1"])
    new = dict(conn.execute(
        "SELECT * FROM injuries WHERE player='Star' ORDER BY captured_at DESC LIMIT 1"
    ).fetchone())
    assert new["applied_status"] == "day-to-day"
    assert new["impact"] == 1.6


# ---------- F3: manual 可达 + 闸门尊重手设值 ----------


def test_gate_prefers_manual_impact_over_suggestion(conn, monkeypatch):
    _ins(conn, "g1", 5, "Star", "out", "2026-11-14T18:00:00+00:00", 5.0, source="manual")
    _sug(monkeypatch, [
        {"team_id": 5, "player": "Star", "status": "out", "suggested_impact": 0.5},
    ])
    edges = [{"market": "moneyline", "side": "home", "bettable": True, "bettable_note": None, "gated": None}]
    out = injury.apply_injury_gate(conn, "g1", edges)
    assert out[0]["bettable"] is False  # 手设 5.0 ≥ 3.0 → 拦
    assert "5.0" in out[0]["bettable_note"]


def test_api_impact_write_marks_manual():
    """UI 两条写路径必须落 impact_source='manual'（F3 核心可达性）。"""
    from app import api

    src = json.dumps(_fetch_api_sources(api), ensure_ascii=False)
    # 两个端点的 UPDATE 都要带 impact_source
    assert "impact_source='manual'" in src or 'impact_source="manual"' in src, (
        "api.py 的 impact 写路径没有标记 manual"
    )


def _fetch_api_sources(api):
    import inspect

    return {"set_impact": inspect.getsource(api.set_impact),
            "apply_impact_suggestion": inspect.getsource(api.apply_impact_suggestion)}


# ---------- F4: 闸门只看 out/doubtful ----------


def test_gate_ignores_questionable_only_totals(conn, monkeypatch):
    """全是 questionable/day-to-day 碎片（哪怕合计 ≥3.0）→ 不拦。"""
    _ins(conn, "g1", 5, "A", "questionable", "2026-11-14T18:00:00+00:00", 0.0)
    _ins(conn, "g1", 5, "B", "day-to-day", "2026-11-14T18:00:00+00:00", 0.0)
    _sug(monkeypatch, [
        {"team_id": 5, "player": "A", "status": "questionable", "suggested_impact": 1.8},
        {"team_id": 5, "player": "B", "status": "day-to-day", "suggested_impact": 1.8},
    ])
    edges = [{"market": "moneyline", "side": "home", "bettable": True, "bettable_note": None, "gated": None}]
    out = injury.apply_injury_gate(conn, "g1", edges)
    assert out[0]["bettable"] is True


def test_gate_counts_out_and_doubtful(conn, monkeypatch):
    _ins(conn, "g1", 5, "A", "out", "2026-11-14T18:00:00+00:00", 0.0)
    _ins(conn, "g1", 5, "B", "doubtful", "2026-11-14T18:00:00+00:00", 0.0)
    _sug(monkeypatch, [
        {"team_id": 5, "player": "A", "status": "out", "suggested_impact": 2.0},
        {"team_id": 5, "player": "B", "status": "doubtful", "suggested_impact": 2.0},
    ])
    edges = [{"market": "moneyline", "side": "home", "bettable": True, "bettable_note": None, "gated": None}]
    out = injury.apply_injury_gate(conn, "g1", edges)
    assert out[0]["bettable"] is False


# ---------- F5: snapshot 记录建议值 ----------


def test_snapshot_includes_suggested_impact(conn, monkeypatch):
    """AUTO_APPLY 关时 impact 存 0，但快照必须带上决策时刻的建议值。"""
    _ins(conn, "g1", 5, "Star", "out", "2026-11-14T18:00:00+00:00", 0.0)
    _sug(monkeypatch, [
        {"team_id": 5, "player": "Star", "status": "out", "suggested_impact": 3.2},
    ])
    snap = injury.snapshot(conn, "g1")
    assert snap[0]["impact"] == 3.2          # 建议值（决策口径）
    assert snap[0]["impact_source"] == "auto"
    row = dict(conn.execute("SELECT impact FROM injuries WHERE player='Star'").fetchone())
    assert row["impact"] == 0.0              # 库里不变


# ---------- F1: predict_game 求和去重（端到端） ----------


def test_predict_game_sums_deduped_impacts(tmp_path, monkeypatch):
    """旧行 impact 残留 + 新行 0 → 求和必须 0（复活球员不再被扣分）。"""
    # 造独立内存库走 predict_game 全流程
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE injuries (
            id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT, team_id INTEGER,
            player TEXT, status TEXT, detail TEXT, impact REAL DEFAULT 0,
            captured_at TEXT, impact_source TEXT DEFAULT 'auto',
            applied_status TEXT, applied_at TEXT,
            UNIQUE(game_id, team_id, player, status)
        );
        CREATE TABLE teams (id INTEGER PRIMARY KEY, abbrev TEXT);
        CREATE TABLE games (
            id TEXT PRIMARY KEY, season TEXT, slate_date TEXT, tipoff TEXT,
            home_id INTEGER, away_id INTEGER, home_score INTEGER, away_score INTEGER,
            status TEXT, updated_at TEXT
        );
        CREATE TABLE odds_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT, book TEXT,
            market TEXT, side TEXT, line REAL, price REAL, captured_at TEXT,
            last_seen_at TEXT, source TEXT DEFAULT 'espn',
            UNIQUE(game_id, book, market, side, line, price, captured_at)
        );
        CREATE TABLE predictions (
            id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT, created_at TEXT,
            pred_margin REAL, sigma_margin REAL, pred_total REAL, sigma_total REAL,
            home_win_prob REAL, details TEXT
        );
        CREATE TABLE signal_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT, game_id TEXT, slate_date TEXT,
            market TEXT, side TEXT, line REAL, p_model REAL, p_calibrated REAL,
            p_fair REAL, p_implied REAL, price_best REAL, price_consensus REAL,
            price_closing REAL, closing_line REAL, ev REAL, edge REAL,
            bettable INTEGER DEFAULT 0, book TEXT, gated TEXT, result TEXT,
            profit REAL, captured_at TEXT, created_at TEXT, injuries TEXT,
            UNIQUE(game_id, market, side, bettable)
        );
        INSERT INTO teams VALUES(5, 'CLE'), (6, 'DAL');
        INSERT INTO games VALUES('g1', '2026-27', '2026-11-14', '2026-11-15T00:30:00Z',
                                 5, 6, NULL, NULL, 'STATUS_SCHEDULED', 'x');
        """
    )
    # 复活场景：out 行 impact 3.2（auto 采纳过）→ 球员复活出现新行（无建议）
    _ins(conn, "g1", 5, "Star", "out", "2026-11-14T18:00:00+00:00", 3.2)
    _ins(conn, "g1", 5, "Star", "day-to-day", "2026-11-14T20:00:00+00:00", 0.0)
    monkeypatch.setattr(injury, "_suggestions", lambda conn, gid, model=None: [])
    monkeypatch.setattr(injury, "_home_team_id", lambda conn, gid: 5)
    monkeypatch.setattr(injury, "_away_team_id", lambda conn, gid: 6)

    from app.jobs import predict as predict_mod
    from app.models import ratings as ratings_mod

    game = dict(conn.execute("SELECT * FROM games WHERE id='g1'").fetchone())
    model = {"teams": {}, "n_games": 0, "created_at": "x", "names": {},
             "margin_intercept": 0, "margin_elo": 0, "margin_rest": 0,
             "total_intercept": 0, "sigma_margin": 10, "sigma_total": 13}
    pred = predict_mod.predict_game(conn, game, model, {}, {}, {})
    comp = pred["components"]
    # 去重后 Star 的最新行 impact=0 → 求和 0（旧行 3.2 不计入）
    assert comp["injury_home"] == 0.0, f"幽灵伤停：injury_home={comp['injury_home']}"
    conn.close()
