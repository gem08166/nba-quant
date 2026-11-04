"""bettable 闸门与同场互斥的单元测试。"""
from app.jobs.predict import _bettable_reason, build_market


def _edge(market, side, book="draftkings", ev=0.08):
    return {"market": market, "side": side, "book": book, "ev": ev, "signal": True}


def test_soft_book_downgraded():
    e = _edge("moneyline", "home", book="1xbet")
    reason = _bettable_reason(e, n_books=10, best_book="1xbet")
    assert reason and "软书" in reason


def test_shallow_consensus_downgraded():
    e = _edge("spread", "home", book="draftkings")
    reason = _bettable_reason(e, n_books=2, best_book="draftkings")
    assert reason and "共识" in reason


def test_deep_consensus_passes():
    e = _edge("spread", "away", book="draftkings")
    assert _bettable_reason(e, n_books=5, best_book="draftkings") is None


def test_mutual_exclusive_spread_vs_ml():
    """同场让分与胜负都触发时，只有 EV 最高的一条可下注。"""
    odds_rows = [
        {"book": "Draft Kings", "market": "spread", "side": "home", "line": -5.5, "price": 1.91,
         "captured_at": "2026-11-04T05:00:00+00:00", "last_seen_at": "2026-11-04T05:00:00+00:00"},
        {"book": "Draft Kings", "market": "spread", "side": "away", "line": 5.5, "price": 1.91,
         "captured_at": "2026-11-04T05:00:00+00:00", "last_seen_at": "2026-11-04T05:00:00+00:00"},
        {"book": "Draft Kings", "market": "total", "side": "over", "line": 220.5, "price": 1.91,
         "captured_at": "2026-11-04T05:00:00+00:00", "last_seen_at": "2026-11-04T05:00:00+00:00"},
        {"book": "Draft Kings", "market": "total", "side": "under", "line": 220.5, "price": 1.91,
         "captured_at": "2026-11-04T05:00:00+00:00", "last_seen_at": "2026-11-04T05:00:00+00:00"},
        {"book": "Draft Kings", "market": "moneyline", "side": "home", "line": None, "price": 1.60,
         "captured_at": "2026-11-04T05:00:00+00:00", "last_seen_at": "2026-11-04T05:00:00+00:00"},
        {"book": "Draft Kings", "market": "moneyline", "side": "away", "line": None, "price": 2.37,
         "captured_at": "2026-11-04T05:00:00+00:00", "last_seen_at": "2026-11-04T05:00:00+00:00"},
    ]
    pred = {"pred_margin": 14.0, "pred_total": 224.0, "sigma_margin": 10.8, "sigma_total": 14.0}
    _, market = build_market(pred, odds_rows)
    bettable = [e for e in market["edges"] if e.get("bettable")]
    # 模型 -14 分对 -5.5 让分和 ML 都是大幅看好 home → 两个市场都触发，
    # 互斥后最多一条 spread/ML 可下注
    corr_markets = {e["market"] for e in bettable if e["market"] in ("spread", "moneyline")}
    assert len(corr_markets) <= 1
    # total 不受互斥影响（若触发，仍可下注）


def test_sharp_anchor_off_by_default():
    odds_rows = [
        {"book": "Pinnacle", "market": "moneyline", "side": "home", "line": None, "price": 1.55,
         "captured_at": "2026-11-04T05:00:00+00:00", "last_seen_at": "2026-11-04T05:00:00+00:00"},
        {"book": "Pinnacle", "market": "moneyline", "side": "away", "line": None, "price": 2.50,
         "captured_at": "2026-11-04T05:00:00+00:00", "last_seen_at": "2026-11-04T05:00:00+00:00"},
        {"book": "SoftBook", "market": "moneyline", "side": "home", "line": None, "price": 1.70,
         "captured_at": "2026-11-04T05:00:00+00:00", "last_seen_at": "2026-11-04T05:00:00+00:00"},
        {"book": "SoftBook", "market": "moneyline", "side": "away", "line": None, "price": 2.20,
         "captured_at": "2026-11-04T05:00:00+00:00", "last_seen_at": "2026-11-04T05:00:00+00:00"},
    ]
    from app.jobs.predict import consensus_market

    cons = consensus_market(odds_rows)
    ml = cons["moneyline"]
    # SHARP_ANCHOR 默认关闭：fair 仍是全体平均
    assert "sharp_anchor" not in ml
