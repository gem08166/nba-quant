"""POST /api/bets/clear：范围 + 确认文本 + 自动备份。"""
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient

from app.api import get_conn, router


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_dir = tmp_path / "data"
    db_dir.mkdir()
    db_file = db_dir / "test.db"
    from app import config

    monkeypatch.setattr(config, "DATA_DIR", db_dir)
    import app.api as api_mod

    monkeypatch.setattr(api_mod, "DATA_DIR", db_dir)

    conn = sqlite3.connect(str(db_file))
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE teams (id INTEGER PRIMARY KEY, abbrev TEXT, name TEXT);
        CREATE TABLE games (
            id TEXT PRIMARY KEY, slate_date TEXT, status TEXT,
            home_id INTEGER, away_id INTEGER,
            home_score INTEGER, away_score INTEGER, tipoff TEXT
        );
        INSERT INTO teams VALUES (1,'LAL','Lakers'),(2,'BOS','Celtics');
        INSERT INTO games VALUES ('g1','2026-11-06','STATUS_FINAL',2,1,100,90,NULL);
        CREATE TABLE bets (
            id INTEGER PRIMARY KEY, game_id TEXT, market TEXT, side TEXT, line REAL,
            price REAL, stake REAL, model_prob REAL, ev REAL, placed_at TEXT,
            result TEXT DEFAULT 'open', profit REAL, closing_line REAL, closing_price REAL,
            clv REAL, clv_line REAL, note TEXT, ref TEXT
        );
        INSERT INTO bets(game_id, market, side, line, price, stake, model_prob, ev, placed_at, result, note)
        VALUES ('g1','spread','home',-2.5,1.95,2.0,0.55,0.07,'2026-11-06T10:00:00+00:00','open','自定义'),
               ('g1','total','over',220.5,1.90,1.0,0.52,0.0,'2026-11-06T10:10:00+00:00','lost','回填 赛前信号'),
               ('g1','total','under',210.5,1.90,1.0,0.52,0.0,'2026-11-06T10:20:00+00:00','won','回填 可下注');
        """
    )
    conn.commit()
    conn.close()

    def fake_get_conn():
        c = sqlite3.connect(str(db_file), timeout=30, check_same_thread=False)
        c.row_factory = sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    from fastapi import FastAPI

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_conn] = fake_get_conn
    yield TestClient(app), db_dir
    app.dependency_overrides.clear()


def test_clear_requires_confirm_text(client):
    tc, _ = client
    resp = tc.post("/api/bets/clear", json={"scope": "all", "confirm": "wrong"})
    assert resp.status_code == 400
    resp = tc.post("/api/bets/clear", json={"scope": "backfill", "confirm": "CLEAR"})
    assert resp.status_code == 400


def test_clear_backfill_only(client):
    tc, _ = client
    resp = tc.post("/api/bets/clear", json={"scope": "backfill", "confirm": "回填"})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 2
    remaining = tc.get("/api/bets?game_id=g1").json()["bets"]
    assert len(remaining) == 1
    assert remaining[0]["note"] == "自定义"


def test_clear_all_with_double_confirm(client):
    tc, _ = client
    resp = tc.post("/api/bets/clear", json={"scope": "all", "confirm": "CLEAR"})
    assert resp.status_code == 200
    assert resp.json()["deleted"] == 3
    assert tc.get("/api/bets?game_id=g1").json()["bets"] == []


def test_clear_backs_up_to_data_dir(client):
    tc, db_dir = client
    resp = tc.post("/api/bets/clear", json={"scope": "all", "confirm": "CLEAR"})
    assert resp.status_code == 200
    backups = list((db_dir / "backup").glob("bets-clear-*.json"))
    assert len(backups) == 1
    payload = json.loads(backups[0].read_text(encoding="utf-8"))
    assert payload["count"] == 3
    assert payload["scope"] == "all"
    assert len(payload["bets"]) == 3
