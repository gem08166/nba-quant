"""注单分场景：备注含「滚球」的算滚球注单，单独统计。"""
import sqlite3

from app.jobs.settle import bet_kind, bet_source, summarize


def test_bet_kind():
    assert bet_kind({"note": "滚球 大小 大 220.5 @1.90 · 好体育 q2 11:13"}) == "live"
    assert bet_kind({"note": "回填 赛前信号 · hao-tiyu · EV +8.0% · 2026-11-04"}) == "backfill"
    assert bet_kind({"note": "手动 大小 大 218.5 @1.95 · 2026-11-04"}) == "pregame"
    assert bet_kind({"note": "draftkings / model 61.9% / fair 56.2%"}) == "pregame"
    assert bet_kind({"note": None}) == "pregame"
    assert bet_kind({}) == "pregame"


def test_bet_source():
    """真实 vs 模拟：只有回填算模拟。"""
    assert bet_source({"note": "回填 赛前信号 · x · 2026-11-04"}) == "sim"
    assert bet_source({"note": "滚球 大小 大 220.5 @1.90"}) == "mine"
    assert bet_source({"note": "手动 让分 主 -3.5 @1.91"}) == "mine"
    assert bet_source({"note": "draftkings / model 61.9%"}) == "mine"


def _conn():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE bets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            game_id TEXT, market TEXT, side TEXT, line REAL, price REAL,
            stake REAL, model_prob REAL, ev REAL, result TEXT, profit REAL,
            clv REAL, clv_line REAL, placed_at TEXT, note TEXT, ref TEXT
        );
        """
    )
    rows = [
        # 赛前：2 胜 1 负
        ("spread", "home", 5.0, "won", 4.0, 0.02, "信号 A"),
        ("total", "over", 2.0, "won", 1.8, 0.01, "信号 B"),
        ("moneyline", "away", 3.0, "lost", -3.0, -0.03, "信号 C"),
        # 滚球：1 胜 1 负 + 1 未结算
        ("total", "over", 2.0, "won", 1.9, None, "滚球 大小 大 220.5 @1.90 · 好体育 q2 11:13"),
        ("spread", "home", 1.0, "lost", -1.0, None, "滚球 让分 主 -6.5 @1.95 · 好体育 q1 00:29"),
        ("spread", "away", 1.0, "open", None, None, "滚球 让分 客 +6.5 @1.81 · 好体育 q2 05:00"),
    ]
    for market, side, stake, result, profit, clv, note in rows:
        conn.execute(
            "INSERT INTO bets(game_id, market, side, line, price, stake, model_prob, ev,"
            " result, profit, clv, placed_at, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            ("g1", market, side, 1.5, 1.9, stake, 0.55, 0.05, result, profit, clv, "2026-11-06", note),
        )
    conn.commit()
    return conn


def test_summarize_splits_kinds():
    summary = summarize(_conn())
    assert summary["settled"] == 5
    assert summary["open"] == 1

    pre = summary["by_kind"]["pregame"]
    live = summary["by_kind"]["live"]

    assert pre["settled"] == 3
    assert pre["open"] == 0
    assert round(pre["profit"], 2) == 2.8  # 4.0 + 1.8 - 3.0
    assert round(pre["stake"], 2) == 10.0

    assert live["settled"] == 2
    assert live["open"] == 1
    assert round(live["profit"], 2) == 0.9  # 1.9 - 1.0
    assert round(live["stake"], 2) == 3.0


def test_overall_still_matches_total():
    summary = summarize(_conn())
    assert round(summary["profit"], 2) == 3.7  # 2.8 + 0.9
    assert summary["by_kind"]["pregame"]["settled"] + summary["by_kind"]["live"]["settled"] == summary["settled"]


def test_by_source_splits_real_vs_simulated():
    """真实 = 赛前 + 滚球；模拟 = 回填。"""
    conn = _conn()
    conn.execute(
        "INSERT INTO bets(game_id, market, side, line, price, stake, model_prob, ev,"
        " result, profit, placed_at, note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        ("g2", "total", "over", 220.5, 1.9, 1.0, 0.55, 0.05, "lost", -1.0, "2026-11-06",
         "回填 赛前信号 · sbr · EV +5.0% · 2026-11-06"),
    )
    conn.commit()
    summary = summarize(conn)
    assert summary["by_source"]["mine"]["settled"] == 5
    assert summary["by_source"]["sim"]["settled"] == 1
    assert round(summary["by_source"]["sim"]["profit"], 2) == -1.0
    assert round(summary["by_source"]["mine"]["profit"], 2) == 3.7
