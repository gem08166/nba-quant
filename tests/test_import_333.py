"""333 订单解析：原始 API 响应 / 订单数组 / 紧凑数组三种形状，以及未结算单。"""
import sqlite3

from scripts.import_333_bets import normalize_payload, team_abbrev, to_bet


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE teams (id INTEGER PRIMARY KEY, abbrev TEXT, name TEXT);
        CREATE TABLE games (
            id TEXT PRIMARY KEY, slate_date TEXT, status TEXT,
            home_id INTEGER, away_id INTEGER,
            home_score INTEGER, away_score INTEGER, tipoff TEXT
        );
        INSERT INTO teams VALUES (1,'PHX','Suns'),(2,'ORL','Magic');
        INSERT INTO games VALUES ('g1','2026-11-09','STATUS_FINAL',1,2,110,100,NULL);
        """
    )
    return conn


def _raw_order(order_id="o1", open_bet=False):
    return {
        "orderId": order_id,
        "betTime": "2026-11-10 09:00:00",
        "ante": "2.00000000",
        "netWinAmount": "1.86" if not open_bet else "0",
        "orderStatus": 1,
        "result": 1,
        "gameContent": {
            "parlayBet": False,
            "ante": "2.00000000",
            "detail": [
                {
                    "homeName": "菲尼克斯太阳",
                    "awayName": "奥兰多魔术",
                    "kickOffTime": "2026-11-10 10:00:00",
                    "market": "ou",
                    "betOn": "ov",
                    "k": "220.5",
                    "odds": "1.93",
                    "won": "1",
                    "inplay": False,
                }
            ],
        },
    }


def test_team_abbrev_uses_suffix():
    assert team_abbrev("菲尼克斯太阳") == "PHX"  # 不是 NY（尼克斯）
    assert team_abbrev("纽约尼克斯") == "NY"


def test_normalize_raw_api_response_marks_open():
    payload = {
        "code": 0,
        "data": {
            "settlement": {"data": [_raw_order("s1")]},
            "unSettlement": {"data": [_raw_order("u1", open_bet=True)]},
        },
    }
    records = normalize_payload(payload)
    assert len(records) == 2
    settled = next(r for r in records if r["orderId"] == "s1")
    open_bet = next(r for r in records if r["orderId"] == "u1")
    assert settled["open"] is False
    assert open_bet["open"] is True
    assert open_bet["legs"][0]["market"] == "ou"
    assert open_bet["legs"][0]["betOn"] == "ov"


def test_normalize_accepts_raw_list_and_compact_list():
    raw = normalize_payload([_raw_order("r1")])
    assert raw[0]["orderId"] == "r1"
    compact = normalize_payload([{"orderId": "c1", "ante": 2, "legs": []}])
    assert compact[0]["orderId"] == "c1"


def test_to_bet_open_bet_has_no_profit():
    conn = _conn()
    rec = normalize_payload({"data": {"unSettlement": {"data": [_raw_order("u1", open_bet=True)]}}})[0]
    bet, reason = to_bet(conn, rec)
    assert reason is None, reason
    assert bet["result"] == "open"
    assert bet["profit"] is None
    assert bet["game_id"] == "g1"
    assert bet["market"] == "total" and bet["side"] == "over"
    assert bet["line"] == 220.5
    assert bet["ref"] == "hao-u1"


def test_to_bet_settled_uses_net_win():
    conn = _conn()
    rec = normalize_payload([_raw_order("s1")])[0]
    bet, reason = to_bet(conn, rec)
    assert reason is None, reason
    assert bet["result"] == "won"
    assert bet["profit"] == 1.86
