import pytest

from app.jobs.settle import _line_clv, outcome


def _bet(market: str, side: str, line: float | None = None) -> dict:
    return {"market": market, "side": side, "line": line}


def test_spread_home_favorite():
    # home -5.5, home wins by 10 -> cover
    assert outcome(_bet("spread", "home", -5.5), 110, 100) == "won"
    # home -10, wins by 10 -> push
    assert outcome(_bet("spread", "home", -10), 110, 100) == "push"
    # home -15, wins by 10 -> no cover
    assert outcome(_bet("spread", "home", -15), 110, 100) == "lost"


def test_spread_away_underdog():
    # away +8.5, home wins by 4 -> away covers
    assert outcome(_bet("spread", "away", 8.5), 104, 100) == "won"
    # away +3, home wins by 4 -> no cover
    assert outcome(_bet("spread", "away", 3), 104, 100) == "lost"


def test_total():
    assert outcome(_bet("total", "over", 219.5), 112, 110) == "won"
    assert outcome(_bet("total", "under", 223), 112, 110) == "won"
    assert outcome(_bet("total", "over", 222), 112, 110) == "push"
    assert outcome(_bet("total", "under", 220), 112, 110) == "lost"


def test_moneyline():
    assert outcome(_bet("moneyline", "away"), 110, 117) == "won"
    assert outcome(_bet("moneyline", "home"), 112, 117) == "lost"


def test_line_clv_is_sign_correct():
    # Positive means the bettor got a better number than the close.
    assert _line_clv(_bet("spread", "home", -3), -5) == pytest.approx(2)
    assert _line_clv(_bet("spread", "away", 8), 6) == pytest.approx(2)
    assert _line_clv(_bet("total", "over", 220), 222) == pytest.approx(2)
    assert _line_clv(_bet("total", "under", 222), 220) == pytest.approx(2)
    # A worse number is negative.
    assert _line_clv(_bet("spread", "home", -5), -3) == pytest.approx(-2)
    # No line / moneyline -> no line CLV.
    assert _line_clv(_bet("moneyline", "home"), 0) is None
    assert _line_clv(_bet("spread", "home", -5), None) is None
