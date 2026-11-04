"""跨季季前赛锚定的单元测试：合成两个赛季的 DB 验证分支触发与方向。"""
import sqlite3
from datetime import date, timedelta

import pytest


@pytest.fixture()
def two_season_db(tmp_path):
    """2025-26 常规赛 80 场（均分 231）+ 2026-27 季前赛 20 场（均分 221，得分下行）。"""
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
    d0 = date(2025, 10, 1)
    # 上赛季常规赛：80 场，total = 231 ± 0
    for i in range(80):
        home = (i % 8) + 1
        away = ((i + 3) % 8) + 1
        if home == away:
            away = (away % 8) + 1
        day = (d0 + timedelta(days=i)).isoformat()
        conn.execute(
            "INSERT INTO games VALUES (?, '2025-26', 2, ?, ?, ?, ?, ?, ?, 'STATUS_FINAL', NULL, NULL, ?)",
            (f"p{i}", day, f"{day}T00:00:00Z", home, away, 116, 115, "2025-10-01T00:00:00Z"),
        )
    # 季前赛：20 场，total = 221（比上赛季低 10 分）
    p0 = date(2026, 10, 1)
    for i in range(20):
        home = (i % 8) + 1
        away = ((i + 2) % 8) + 1
        if home == away:
            away = (away % 8) + 1
        day = (p0 + timedelta(days=i)).isoformat()
        conn.execute(
            "INSERT INTO games VALUES (?, '2026-27', 1, ?, ?, ?, ?, ?, ?, 'STATUS_FINAL', NULL, NULL, ?)",
            (f"pre{i}", day, f"{day}T00:00:00Z", home, away, 111, 110, "2026-10-01T00:00:00Z"),
        )
    # 新赛季常规赛开局：20 场，total = 221（与季前赛一致，比上赛季低 10）
    r0 = date(2026, 10, 20)
    for i in range(20):
        home = (i % 8) + 1
        away = ((i + 4) % 8) + 1
        if home == away:
            away = (away % 8) + 1
        day = (r0 + timedelta(days=i)).isoformat()
        conn.execute(
            "INSERT INTO games VALUES (?, '2026-27', 2, ?, ?, ?, ?, ?, ?, 'STATUS_FINAL', NULL, NULL, ?)",
            (f"r{i}", day, f"{day}T00:00:00Z", home, away, 111, 110, "2026-10-20T00:00:00Z"),
        )
    conn.commit()
    conn.close()
    return db_path


def _connect(db_path):
    def _c(path=None):
        c = sqlite3.connect(db_path)
        c.row_factory = sqlite3.Row
        return c

    return _c


def test_season_carry_triggers_and_points_right(two_season_db, monkeypatch):
    """跨季 fit 应触发季前赛锚定，shift 指向"总分下降"方向（负值）。"""
    import app.models.ratings as ratings

    # fit(conn, ...) 直接接收连接：不 patch db.connect，patch load_finals 用的 conn
    conn = _connect(two_season_db)()
    model = ratings.fit(conn, as_of="2026-10-20")

    # gap = 2026-10-20 - 2026-01-19(第80天) > 10 → 跨季分支
    assert model["total_season_carry_shift"] != 0.0
    # 季前赛均分 221 vs 模型预测（基于 231 训练）应偏低 → shift < 0
    assert model["total_season_carry_shift"] < 0


def test_season_carry_off_keeps_old_behavior(two_season_db, monkeypatch):
    """carry=0 时 shift=0，intercept 与旧逻辑一致。"""
    import app.models.ratings as ratings

    monkeypatch.setattr(ratings, "TOTAL_REGIME_SEASON_CARRY", 0.0)
    conn = _connect(two_season_db)()
    model = ratings.fit(conn, as_of="2026-10-20")
    assert model["total_season_carry_shift"] == 0.0


def test_carry_reduces_opening_bias(two_season_db, monkeypatch):
    """新赛季总分确实比上赛季低 10 分：开启 carry 后开局预测 bias 应更小。"""
    import numpy as np

    from app.models import ratings

    games_conn = _connect(two_season_db)()
    games_conn.row_factory = sqlite3.Row
    new_season = [dict(g) for g in games_conn.execute(
        "SELECT * FROM games WHERE season='2026-27' AND season_type=2 ORDER BY slate_date"
    )]

    def _opening_bias(carry):
        monkeypatch.setattr(ratings, "TOTAL_REGIME_SEASON_CARRY", carry)
        conn2 = _connect(two_season_db)()
        model = ratings.fit(conn2, as_of="2026-10-20")
        elo = {t: 1500.0 for t in range(1, 9)}
        errs = []
        for g in new_season[:5]:
            p = ratings.predict(model, int(g["home_id"]), int(g["away_id"]),
                                elo[int(g["home_id"])], elo[int(g["away_id"])], 7, 7)
            errs.append(p["pred_total"] - (g["home_score"] + g["away_score"]))
        return abs(float(np.mean(errs)))

    off = _opening_bias(0.0)
    on = _opening_bias(1.0)
    assert on < off, f"carry=1.0 的 bias ({on:.2f}) 应小于 skip ({off:.2f})"
