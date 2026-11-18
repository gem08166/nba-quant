"""推送去重口径：同场 / 同市场 / 同方向只推一次。

赔率每几分钟抖一次（1.94 → 1.93 → 1.885）、盘口小幅漂移（233.5 → 233.75）
都是同一笔注单的不同报价，不能当成新信号反复推送。
不同方向（over/under、主/客）是不同注单，要各推一次。
"""
import sqlite3

from app.jobs import notify

GAME = {"id": "g1", "tipoff": "2026-11-11T00:00:00+00:00",
        "home_abbrev": "LAL", "away_abbrev": "BOS"}


def _edge(price, side="away", line=-3.5, market="spread"):
    return {
        "market": market, "side": side, "line": line, "price": price,
        "book": "DraftKings", "model_prob": 0.58, "fair_prob": 0.52,
        "edge": 0.06, "ev": 0.13, "stake_pct": 0.02,
        "signal": True, "bettable": True,
    }


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE notifications (
            game_id TEXT, market TEXT, side TEXT, line REAL, price REAL, sent_at TEXT,
            UNIQUE(game_id, market, side, line, price)
        );
        CREATE TABLE bets (
            id INTEGER PRIMARY KEY, game_id TEXT, market TEXT, side TEXT, line REAL,
            price REAL, stake REAL, result TEXT, ref TEXT
        );
        """
    )
    return conn


def _add_bet(conn, market="spread", result="open", game_id="g1"):
    conn.execute(
        "INSERT INTO bets(game_id, market, side, line, price, stake, result, ref) "
        "VALUES(?, ?, ?, ?, ?, ?, ?, ?)",
        (game_id, market, "home", -2.5, 1.9, 2.0, result, "hao-test"),
    )
    conn.commit()


def _patch(monkeypatch):
    monkeypatch.setattr(notify.db, "init_db", lambda conn: None)
    calls = []
    monkeypatch.setattr(notify, "send", lambda title, text: calls.append(text) or True)
    return calls


def test_price_tick_does_not_repush(monkeypatch):
    calls = _patch(monkeypatch)
    conn = _conn()

    assert notify.send_new_signals(conn, [(GAME, _edge(1.94))]) == 1
    assert notify.send_new_signals(conn, [(GAME, _edge(1.93))]) == 0
    assert notify.send_new_signals(conn, [(GAME, _edge(1.885))]) == 0

    assert len(calls) == 1
    rows = conn.execute("SELECT price FROM notifications").fetchall()
    assert [row["price"] for row in rows] == [1.94]  # 只记首次报价


def test_line_drift_does_not_repush(monkeypatch):
    calls = _patch(monkeypatch)
    conn = _conn()

    assert notify.send_new_signals(conn, [(GAME, _edge(1.97, market="total", side="under", line=233.5))]) == 1
    assert notify.send_new_signals(conn, [(GAME, _edge(1.95, market="total", side="under", line=233.75))]) == 0
    assert len(calls) == 1


def test_opposite_side_is_a_new_bet(monkeypatch):
    calls = _patch(monkeypatch)
    conn = _conn()

    assert notify.send_new_signals(conn, [(GAME, _edge(1.94, side="away"))]) == 1
    assert notify.send_new_signals(conn, [(GAME, _edge(1.95, side="home"))]) == 1
    assert len(calls) == 2


def test_different_market_is_a_new_bet(monkeypatch):
    calls = _patch(monkeypatch)
    conn = _conn()

    assert notify.send_new_signals(conn, [(GAME, _edge(1.94, market="spread"))]) == 1
    assert notify.send_new_signals(conn, [(GAME, _edge(2.25, market="moneyline", line=None))]) == 1
    assert len(calls) == 2


def test_batch_within_one_push_dedupes_same_bet(monkeypatch):
    calls = _patch(monkeypatch)
    conn = _conn()

    sent = notify.send_new_signals(
        conn,
        [(GAME, _edge(1.94)), (GAME, _edge(1.93)), (GAME, _edge(1.92))],
    )
    assert sent == 1
    assert len(calls) == 1
    assert calls[0].count("可下注") == 1


def test_open_bet_on_same_market_is_not_pushed(monkeypatch):
    """同场同市场已有在台单 → 不推送（即使方向/盘口不同，2026-11-19 用户规则）。"""
    calls = _patch(monkeypatch)
    conn = _conn()
    _add_bet(conn, market="spread", result="open")

    # 反向（home 已有单，信号是 away）也不推
    assert notify.send_new_signals(conn, [(GAME, _edge(1.94, side="away"))]) == 0
    assert len(calls) == 0

    # 不同市场照常推
    assert notify.send_new_signals(conn, [(GAME, _edge(1.95, market="total", side="under", line=233.5))]) == 1


def test_settled_bet_on_same_market_still_pushes(monkeypatch):
    """已结算的单不占位：新的一天/新盘口该推还推。"""
    calls = _patch(monkeypatch)
    conn = _conn()
    _add_bet(conn, market="spread", result="won")

    assert notify.send_new_signals(conn, [(GAME, _edge(1.94))]) == 1
    assert len(calls) == 1
