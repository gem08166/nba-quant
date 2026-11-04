"""holdout_eval 的单元测试：合成 DB 验证切分正确性与冻结性。"""
import sqlite3

import pytest


@pytest.fixture()
def synth_db(tmp_path):
    """Build a synthetic DB with 60 finals across 60 days (one game per day)."""
    db_path = tmp_path / "nba.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE teams (id INTEGER PRIMARY KEY, abbrev TEXT UNIQUE, name TEXT, logo TEXT);
        CREATE TABLE games (
          id TEXT PRIMARY KEY, season TEXT, season_type INTEGER, slate_date TEXT,
          tipoff TEXT, home_id INTEGER, away_id INTEGER, home_score INTEGER,
          away_score INTEGER, status TEXT, status_detail TEXT, venue TEXT, updated_at TEXT
        );
        """
    )
    for t in range(1, 9):
        conn.execute("INSERT INTO teams VALUES (?, ?, ?, null)", (t, f"T{t}", f"Team {t}"))
    # 60 games, 8 teams, deterministic score = 100 + home_id - away_id
    from datetime import date, timedelta

    d0 = date(2026, 1, 1)
    for i in range(60):
        home = (i % 8) + 1
        away = ((i + 3) % 8) + 1
        if home == away:
            away = (away % 8) + 1
        day = (d0 + timedelta(days=i)).isoformat()
        conn.execute(
            "INSERT INTO games VALUES (?, '2026-27', 2, ?, ?, ?, ?, ?, ?, 'STATUS_FINAL', NULL, NULL, ?)",
            (f"g{i}", day, f"{day}T00:00:00Z", home, away, 100 + home - away + 3, 100, "2026-01-01T00:00:00Z"),
        )
    conn.commit()
    conn.close()
    return db_path


def test_holdout_split_is_temporal_and_disjoint(synth_db, monkeypatch):
    import app.config as config
    import scripts.holdout_eval as he

    def _connect(path=None):
        c = sqlite3.connect(synth_db)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(he.db, "connect", _connect)
    monkeypatch.setattr(he, "DATA_DIR", synth_db.parent)
    rep = he.run(split=0.7, min_games=50)

    assert "error" not in rep, rep.get("error")
    # 切分是按时间的：训练期最后一天 < 测试期第一天
    assert rep["train_period"][1] < rep["test_period"][0]
    # 训练 + 测试 = 全部
    assert rep["train_games"] + rep["test_games"] == 60
    # 70/30
    assert rep["train_games"] == pytest.approx(42, abs=3)
    # 测试指标结构完整
    t = rep["test"]
    for key in ("n", "margin_mae", "total_mae", "brier", "ece", "home_win_accuracy"):
        assert key in t
    assert t["n"] == rep["test_games"]
    # 基线存在
    assert rep["baseline"]["constant_brier"] == pytest.approx(0.25)


def test_holdout_train_sigma_close_to_model_sigma(synth_db, monkeypatch):
    import app.config as config
    import scripts.holdout_eval as he

    def _connect(path=None):
        c = sqlite3.connect(synth_db)
        c.row_factory = sqlite3.Row
        return c

    monkeypatch.setattr(he.db, "connect", _connect)
    monkeypatch.setattr(he, "DATA_DIR", synth_db.parent)
    rep = he.run(split=0.7, min_games=50)
    # 模型 σ = raw σ × OOS scale，且带下限；合成数据 σ 很小会触底 10.5 floor。
    # 只验证"模型 σ ≥ 训练期残差 σ"（OOS 放大 + floor 的方向正确性）。
    assert rep["model_sigma_margin"] >= rep["train_sigma_margin"] - 0.1
