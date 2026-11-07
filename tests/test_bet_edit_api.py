"""PATCH /api/bets/{id} 编辑注单：未结算可改盘口，已结算只能改结果/盈亏。"""
import pytest
from fastapi.testclient import TestClient

from app.api import get_conn, router


def _seed(conn) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS teams (id INTEGER PRIMARY KEY, abbrev TEXT, name TEXT);
        CREATE TABLE IF NOT EXISTS games (
            id TEXT PRIMARY KEY, slate_date TEXT, status TEXT,
            home_id INTEGER, away_id INTEGER,
            home_score INTEGER, away_score INTEGER, tipoff TEXT
        );
        INSERT INTO teams VALUES (1,'LAL','Lakers'),(2,'BOS','Celtics');
        INSERT INTO games VALUES ('g1','2026-11-06','STATUS_FINAL',2,1,100,90,NULL);
        CREATE TABLE IF NOT EXISTS bets (
            id INTEGER PRIMARY KEY, game_id TEXT, market TEXT, side TEXT, line REAL,
            price REAL, stake REAL, model_prob REAL, ev REAL, placed_at TEXT,
            result TEXT DEFAULT 'open', profit REAL, closing_line REAL, closing_price REAL,
            clv REAL, clv_line REAL, note TEXT, ref TEXT
        );
        INSERT INTO bets(id, game_id, market, side, line, price, stake, model_prob, ev,
                         placed_at, result)
        VALUES (1, 'g1', 'spread', 'home', -2.5, 1.95, 2.0, 0.55, 0.07,
                '2026-11-06T10:00:00+00:00', 'open');
        INSERT INTO bets(id, game_id, market, side, line, price, stake, model_prob, ev,
                         placed_at, result, profit, clv)
        VALUES (2, 'g1', 'total', 'over', 220.5, 1.90, 1.0, 0.52, 0.0,
                '2026-11-06T10:10:00+00:00', 'lost', -1.0, 0.01);
        """
    )
    conn.commit()


@pytest.fixture()
def client(tmp_path, monkeypatch):
    # 独立的临时库 + dependency_overrides 替换 get_conn，不碰真实数据库
    db_file = tmp_path / "test.db"
    import sqlite3 as _sqlite3

    from fastapi import FastAPI

    conn = _sqlite3.connect(str(db_file))
    conn.row_factory = _sqlite3.Row
    _seed(conn)
    conn.close()

    def fake_get_conn():
        c = _sqlite3.connect(str(db_file), timeout=30, check_same_thread=False)
        c.row_factory = _sqlite3.Row
        try:
            yield c
        finally:
            c.close()

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_conn] = fake_get_conn
    yield TestClient(app)
    app.dependency_overrides.clear()


def test_edit_open_bet_market_and_line(client):
    resp = client.patch(
        "/api/bets/1",
        json={"market": "total", "side": "under", "line": 221.5, "price": 1.92, "stake": 3.0},
    )
    assert resp.status_code == 200, resp.text
    row = next(
        b for b in client.get("/api/bets?game_id=g1").json()["bets"] if b["id"] == 1
    )
    assert (row["market"], row["side"], row["line"], row["price"], row["stake"]) == (
        "total",
        "under",
        221.5,
        1.92,
        3.0,
    )


def test_edit_side_must_match_market(client):
    resp = client.patch("/api/bets/1", json={"side": "over"})
    assert resp.status_code == 400


def test_edit_moneyline_rejects_line(client):
    resp = client.patch("/api/bets/1", json={"market": "moneyline", "side": "home", "line": 0.0})
    assert resp.status_code == 200


def test_settled_bet_cannot_change_market(client):
    resp = client.patch("/api/bets/2", json={"market": "spread"})
    assert resp.status_code == 400


def test_settled_bet_can_change_result_and_profit(client):
    resp = client.patch("/api/bets/2", json={"result": "won", "profit": 0.9})
    assert resp.status_code == 200
    row = next(b for b in client.get("/api/bets?game_id=g1").json()["bets"] if b["id"] == 2)
    assert (row["result"], row["profit"]) == ("won", 0.9)


def test_reopen_clears_settlement_fields(client):
    resp = client.patch("/api/bets/2", json={"result": "open"})
    assert resp.status_code == 200
    row = next(b for b in client.get("/api/bets?game_id=g1").json()["bets"] if b["id"] == 2)
    assert row["result"] == "open"
    assert row["profit"] is None
    assert row["clv"] is None


def test_stake_rescale_keeps_profit_sign(client):
    resp = client.patch("/api/bets/2", json={"stake": 2.0})
    assert resp.status_code == 200
    row = next(b for b in client.get("/api/bets?game_id=g1").json()["bets"] if b["id"] == 2)
    assert row["stake"] == 2.0
    assert row["profit"] == -2.0  # -1.0 × (2.0/1.0)


def test_empty_patch_rejected(client):
    resp = client.patch("/api/bets/1", json={})
    assert resp.status_code == 400


def test_delete_open_only(client):
    assert client.delete("/api/bets/1").status_code == 200
    # 已结算需要 force=true
    assert client.delete("/api/bets/2").status_code == 400
    assert client.delete("/api/bets/2?force=true").status_code == 200
    assert client.get("/api/bets?game_id=g1").json()["bets"] == []
