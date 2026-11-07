"""注单导入去重：同一注单只能落一次（ref + 自然键）。"""
import sqlite3

from app.jobs import betssync


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE bets (
            id INTEGER PRIMARY KEY, game_id TEXT, market TEXT, side TEXT, line REAL,
            price REAL, stake REAL, model_prob REAL, ev REAL, placed_at TEXT,
            result TEXT, profit REAL, closing_line REAL, closing_price REAL,
            clv REAL, clv_line REAL, note TEXT, ref TEXT
        );
        CREATE UNIQUE INDEX idx_bets_ref ON bets(ref) WHERE ref IS NOT NULL;
        CREATE TABLE games (
            id TEXT PRIMARY KEY, slate_date TEXT, status TEXT,
            home_id INTEGER, away_id INTEGER,
            home_score INTEGER, away_score INTEGER, tipoff TEXT
        );
        CREATE TABLE teams (id INTEGER PRIMARY KEY, abbrev TEXT, name TEXT);
        INSERT INTO teams VALUES (1,'LAL','Lakers'),(2,'BOS','Celtics');
        INSERT INTO games VALUES ('g1','2026-11-06','STATUS_FINAL',2,1,100,90,NULL);
        """
    )
    return conn


def _bet(**kw):
    row = {
        "game_id": "g1",
        "market": "spread",
        "side": "home",
        "line": -2.5,
        "price": 1.95,
        "stake": 1.0,
        "model_prob": 0.55,
        "placed_at": "2026-11-06T10:00:00+00:00",
        "ref": "srv-1",
    }
    row.update(kw)
    return row


def test_import_same_file_twice_is_idempotent():
    conn = _conn()
    rows = [_bet(ref="srv-1"), _bet(ref="srv-2", line=-4.5)]

    first = betssync.merge_rows(conn, rows)
    assert first["added"] == 2
    assert first["duplicates"] == 0

    second = betssync.merge_rows(conn, rows)
    assert second["added"] == 0
    assert second["duplicates"] == 2
    assert conn.execute("SELECT COUNT(*) FROM bets").fetchone()[0] == 2


def test_natural_key_matches_different_ref_and_time_format():
    conn = _conn()
    betssync.merge_rows(conn, [_bet(ref="srv-1", line=-2.5)])

    # 换 ref、毫秒时间、字符串线：仍是同一注单
    result = betssync.merge_rows(
        conn,
        [
            _bet(
                ref="local-abc",
                line="-2.5",
                placed_at="2026-11-06T10:00:00.000Z",
            )
        ],
    )
    assert result["added"] == 0
    assert result["duplicates"] == 1
    assert conn.execute("SELECT COUNT(*) FROM bets").fetchone()[0] == 1


def test_settled_upgrades_open_but_never_downgrades():
    conn = _conn()
    betssync.merge_rows(conn, [_bet(ref="srv-1")])

    up = betssync.merge_rows(
        conn,
        [_bet(ref="srv-1", result="won", profit=0.95, closing_line=-3.5)],
    )
    assert up["updated"] == 1
    row = conn.execute("SELECT result, profit, closing_line FROM bets").fetchone()
    assert (row["result"], row["profit"], row["closing_line"]) == ("won", 0.95, -3.5)

    down = betssync.merge_rows(conn, [_bet(ref="srv-1")])
    assert down["added"] == 0
    assert down["updated"] == 0
    assert down["duplicates"] == 1
    assert conn.execute("SELECT result FROM bets").fetchone()["result"] == "won"


def test_settled_fills_missing_clv_without_changing_result():
    conn = _conn()
    betssync.merge_rows(conn, [_bet(ref="srv-1", result="won", profit=0.95)])

    result = betssync.merge_rows(
        conn,
        [_bet(ref="srv-1", result="won", profit=0.95, clv=0.02, closing_price=1.9)],
    )
    assert result["updated"] == 1
    row = conn.execute("SELECT result, clv, closing_price FROM bets").fetchone()
    assert row["result"] == "won"
    assert row["clv"] == 0.02
    assert row["closing_price"] == 1.9


def test_duplicate_rows_inside_one_batch_are_skipped():
    conn = _conn()
    result = betssync.merge_rows(conn, [_bet(ref="srv-1"), _bet(ref="local-x")])
    assert result["added"] == 1
    assert result["duplicates"] == 1
    assert conn.execute("SELECT COUNT(*) FROM bets").fetchone()[0] == 1


def test_invalid_rows_are_counted_separately():
    conn = _conn()
    result = betssync.merge_rows(conn, [{"game_id": "g1"}, "nonsense", _bet(ref="srv-1")])
    assert result["added"] == 1
    assert result["invalid"] == 2
    assert result["skipped"] == 2


def test_import_rows_accepts_wrapper_objects():
    conn = _conn()
    wrapped = {"schema": "nbaq.bets/1", "bets": [_bet(ref="srv-1")]}
    assert betssync.import_rows(conn, wrapped)["added"] == 1
    assert betssync.import_rows(conn, {"rows": [_bet(ref="srv-1")]})["duplicates"] == 1
    assert betssync.import_rows(conn, {"nope": 1})["invalid"] == 1


def test_int_id_becomes_srv_ref():
    assert betssync._ref({"id": 7}) == "srv-7"
    assert betssync._ref({"id": "local-1"}) == "local-1"
    assert betssync._ref({"ref": "srv-9", "id": 7}) == "srv-9"


def test_export_import_round_trip_adds_nothing():
    conn = _conn()
    betssync.merge_rows(conn, [_bet(ref="srv-1"), _bet(ref="srv-2", line=-4.5, result="lost", profit=-1.0)])

    payload = betssync.export_payload(conn)
    assert payload["schema"] == "nbaq.bets/1"
    assert payload["count"] == 2
    assert all(row["ref"] for row in payload["bets"])

    again = betssync.import_rows(conn, payload)
    assert again["added"] == 0
    assert again["updated"] == 0
    assert again["duplicates"] == 2


def test_export_keeps_bets_without_ingested_game():
    conn = _conn()
    betssync.merge_rows(conn, [_bet(ref="srv-1", game_id="unknown-game")])

    payload = betssync.export_payload(conn)
    assert payload["count"] == 1
    assert payload["bets"][0]["game_id"] == "unknown-game"
    assert payload["bets"][0]["matchup"] == "unknown-game"
