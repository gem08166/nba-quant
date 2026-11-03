from app.ingest.hao import hk_to_decimal, parse_market
from app.jobs.signal_backtest import settle_edge


def test_hk_to_decimal():
    assert hk_to_decimal("0.89") == 1.89
    assert hk_to_decimal("-0.85") is None  # 负水位非法
    assert hk_to_decimal(None) is None
    # 已是欧赔格式（>=1.3）则原样保留
    assert hk_to_decimal("2.01") == 2.01


def test_parse_market_structure():
    market = {
        "ml": {"h": "1.75", "a": "2.01"},
        "ah": [{"k": "-1.5", "h": "0.89", "a": "0.97"}],
        "ou": [{"k": "218.5", "ov": "0.92", "ud": "0.92"}],
    }
    parsed = parse_market(market)
    assert parsed["ml"] == {"home": 1.75, "away": 2.01}
    assert parsed["ah"][0] == {"line": -1.5, "home_price": 1.89, "away_price": 1.97}
    assert parsed["ou"][0] == {"line": 218.5, "over_price": 1.92, "under_price": 1.92}
    assert parse_market(None) == {}


def test_settle_edge_spread():
    # home -8.5, home wins by 10 -> home covers
    assert settle_edge("spread", "home", -8.5, 110, 100) == "won"
    assert settle_edge("spread", "away", 8.5, 110, 100) == "lost"
    # exact push
    assert settle_edge("spread", "home", -10, 110, 100) == "push"
    # away favored -8.5, away wins by 10 -> away covers
    assert settle_edge("spread", "away", -8.5, 100, 110) == "won"
    assert settle_edge("spread", "home", 8.5, 100, 110) == "lost"


def test_settle_edge_total_and_ml():
    assert settle_edge("total", "over", 218.5, 112, 108) == "won"  # 220 > 218.5
    assert settle_edge("total", "over", 220, 112, 108) == "push"
    assert settle_edge("total", "over", 221, 112, 108) == "lost"
    assert settle_edge("total", "under", 221, 112, 108) == "won"
    assert settle_edge("moneyline", "home", None, 100, 95) == "won"
    assert settle_edge("moneyline", "away", None, 100, 95) == "lost"
