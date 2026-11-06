"""注单分场景：备注含「滚球」的算滚球注单，单独统计。"""
import sqlite3

from app.jobs.settle import bet_kind, summarize


def test_bet_kind():
    assert bet_kind({"note": "滚球 大小 大 220.5 @1.90 · 好体育 q2 11:13"}) == "live"
    assert bet_kind({"note": "draftkings / model 61.9% / fair 56.2%"}) == "pregame"
    assert bet_kind({"note": None}) == "pregame"
    assert bet_kind({}) == "pregame"


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
