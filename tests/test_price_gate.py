"""B 路：价格闸门（默认只记 price_ev 标志；NBAQ_PRICE_GATE=1 才拦单）。"""
from app.jobs import predict as predict_mod


def _edge(fair=0.5, price=1.90, book="draftkings"):
    return {
        "market": "spread",
        "side": "home",
        "line": -5.5,
        "price": price,
        "price_consensus": price,
        "book": book,
        "fair_prob": fair,
    }


def test_price_ev_is_always_recorded(monkeypatch):
    monkeypatch.setattr(predict_mod, "PRICE_GATE", False)
    edge = _edge()
    assert predict_mod._bettable_reason(edge, 5, "draftkings") is None
    assert abs(edge["price_ev"] - (0.5 * 1.90 - 1.0)) < 1e-12


def test_gate_off_does_not_block_negative_price_ev(monkeypatch):
    monkeypatch.setattr(predict_mod, "PRICE_GATE", False)
    assert predict_mod._bettable_reason(_edge(), 5, "draftkings") is None


def test_gate_on_blocks_negative_price_ev(monkeypatch):
    monkeypatch.setattr(predict_mod, "PRICE_GATE", True)
    reason = predict_mod._bettable_reason(_edge(), 5, "draftkings")
    assert reason and "价格未优于公允价" in reason


def test_gate_on_passes_real_price_edge(monkeypatch):
    monkeypatch.setattr(predict_mod, "PRICE_GATE", True)
    # 0.52 × 2.00 − 1 = +4% ≥ 2%
    assert predict_mod._bettable_reason(_edge(fair=0.52, price=2.00), 5, "draftkings") is None


def test_soft_book_still_blocked_regardless_of_price(monkeypatch):
    monkeypatch.setattr(predict_mod, "PRICE_GATE", False)
    reason = predict_mod._bettable_reason(_edge(fair=0.6, price=2.2, book="1xbet"), 5, "1xbet")
    assert reason and "软书" in reason
