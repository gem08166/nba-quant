"""滚球盘口解析：主盘选择与口径（用 2026-11-06 实测样本固定下来）。"""
import pytest

from app.ingest import hao

# 实测样本：第 1 节 04:58，比分 19-17（主 +2），全场总分盘 226.5
SAMPLE = {
    "1x2": {"h": "1.71", "d": "14.00", "a": "2.26"},
    "ah": [
        {"k": "-2.5", "h": "0.81", "a": "0.95", "absK": "-4.5"},
        {"k": "-3.5", "h": "0.98", "a": "0.78", "absK": "-5.5"},
        {"k": "-1.5", "h": "0.68", "a": "1.08", "absK": "-3.5"},
    ],
    "ou": [
        {"ov": "0.89", "ud": "0.87", "k": "226.5"},
        {"ov": "0.81", "ud": "0.95", "k": "225.5"},
        {"ov": "0.98", "ud": "0.78", "k": "227.5"},
    ],
}


def test_picks_most_balanced_handicap_line():
    out = hao.parse_live_market(SAMPLE)
    hc = out["handicap"]
    # -2.5 档两侧 1.81 / 1.95 最接近平水
    assert hc["line"] == -2.5
    assert hc["home"] == pytest.approx(1.81)
    assert hc["away"] == pytest.approx(1.95)


def test_abs_line_equals_line_minus_current_margin():
    """实测口径：absK（从现在起） = k（全场） - 当前分差。"""
    margin = 19 - 17
    for row in SAMPLE["ah"]:
        assert float(row["absK"]) == float(row["k"]) - margin


def test_picks_most_balanced_total_line():
    out = hao.parse_live_market(SAMPLE)
    assert out["total"]["line"] == 226.5
    assert out["total"]["over"] == pytest.approx(1.89)
    assert out["total"]["under"] == pytest.approx(1.87)


def test_moneyline_converted():
    out = hao.parse_live_market(SAMPLE)
    assert out["moneyline"]["home"] == pytest.approx(1.71)
    assert out["moneyline"]["away"] == pytest.approx(2.26)


def test_empty_market_returns_empty():
    assert hao.parse_live_market(None) == {}
    assert hao.parse_live_market({}) == {}
    # 只有占位 ML（0 赔率）时不输出胜负
    assert hao.parse_live_market({"1x2": {"h": "0.00", "a": "0.00"}}) == {}
