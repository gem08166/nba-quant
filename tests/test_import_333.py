"""333 订单解析：原始 API 响应 / 订单数组 / 紧凑数组三种形状，以及未结算单。"""
import sqlite3
from datetime import datetime

from scripts.import_333_bets import find_prunable, normalize_payload, team_abbrev, to_bet


def _bets_conn():
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
        """
    )
    return conn


def _insert_bet(conn, ref, placed_at, result="won", note=""):
    conn.execute(
        "INSERT INTO bets(game_id, market, side, line, price, stake, model_prob, ev, "
        "placed_at, result, profit, note, ref) VALUES('g1','spread','home',-2.5,1.95,2,0.5,0,"
        "?,?,1.9,?,?)",
        (placed_at, result, note, ref),
    )
    conn.commit()


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


def test_authoritative_prunes_only_settled_bets_inside_window():
    conn = _bets_conn()
    _insert_bet(conn, "hao-1", "2026-11-09T12:00:00+08:00")          # 快照内，保留
    _insert_bet(conn, "srv-9", "2026-11-09T13:00:00+08:00")          # 快照内、不在集合 → 删
    _insert_bet(conn, "srv-8", "2026-10-20T13:00:00+08:00")          # 窗口外 → 保留
    _insert_bet(conn, "srv-7", "2026-11-09T13:00:00+08:00", result="open")  # 未结算 → 保留
    _insert_bet(conn, "sim-x", "2026-11-09T13:00:00+08:00", note="模拟 可下注")  # 模拟 → 保留

    times = [datetime.fromisoformat("2026-11-09T12:00:00+08:00")]
    pruned = find_prunable(conn, {"hao-1"}, times)

    assert [r["ref"] for r in pruned] == ["srv-9"]


def test_prune_window_uses_payout_time_not_bet_time():
    """早下单、窗口开始前已结算的单不在快照里，但不是"平台已删除"。

    真实案例：11-05 10:37 下的滚球单在 11-05 结算，11-12 抓单时
    「近 7 日已结」窗口从 11-06 起，快照里没有它——旧逻辑按下注时间
    ±12h 推算窗口，会把它误删（并写墓碑，跨设备生效）。
    """
    conn = _bets_conn()
    _insert_bet(conn, "hao-old", "2026-11-05T10:37:47+08:00")   # 早于结算窗口 → 保留
    _insert_bet(conn, "srv-9", "2026-11-08T13:00:00+08:00")     # 窗口内、不在集合 → 删

    bet_times = [datetime.fromisoformat("2026-11-08T15:00:00+08:00")]
    payouts = [
        datetime.fromisoformat("2026-11-06T12:26:50+08:00"),
        datetime.fromisoformat("2026-11-09T14:00:00+08:00"),
    ]
    pruned = find_prunable(conn, set(), bet_times, True, payouts)

    assert [r["ref"] for r in pruned] == ["srv-9"]
