"""赛后板块：信号历史聚合（去重）与完赛结算。"""
from app.jobs.live import merge_signal_versions, settle_edge  # noqa: F401 (settle via signal_history)
from app.jobs import live as live_mod


def _edge(market="total", side="over", line=214.5, price=1.91, ev=0.10, signal=True, bettable=True, gated=False):
    return {
        "market": market,
        "side": side,
        "line": line,
        "price": price,
        "book": "sbr",
        "model_prob": 0.61,
        "fair_prob": 0.5,
        "edge": ev - 0.02,
        "ev": ev,
        "signal": signal,
        "bettable": bettable,
        "gated": gated,
    }


def _row_json(edges, created="2026-11-04T10:00:00+00:00"):
    import json

    return (created, json.dumps({"edges": edges}))


def test_merge_dedupes_same_signal():
    rows = [
        _row_json([_edge(price=1.90, ev=0.08)], created="2026-11-04T09:00:00+00:00"),
        _row_json([_edge(price=1.95, ev=0.12), _edge(price=1.90, ev=0.08)], created="2026-11-04T10:00:00+00:00"),
        _row_json([_edge(signal=False)], created="2026-11-04T11:00:00+00:00"),
    ]
    out = merge_signal_versions(rows)
    assert len(out) == 1
    sig = out[0]
    assert sig["ev_max"] == 0.12
    assert sig["price_best"] == 1.95
    assert sig["price_last"] == 1.90
    assert sig["n_seen"] == 3  # 信号 False 的不算
    assert sig["first_seen"].startswith("2026-11-04T09")
    assert sig["last_seen"].startswith("2026-11-04T10")
    assert sig["bettable_ever"] is True


def test_merge_separates_different_lines():
    rows = [
        _row_json([_edge(line=214.5), _edge(line=215.5, side="over")]),
    ]
    out = merge_signal_versions(rows)
    assert len(out) == 2
    assert {s["line"] for s in out} == {214.5, 215.5}
    assert all(s["side"] == "over" for s in out)


def test_merge_marks_observed():
    rows = [
        _row_json([_edge(signal=True, bettable=False, gated="分歧闸门拦下")]),
        _row_json([_edge(signal=True, bettable=True, gated=False)], created="2026-11-04T12:00:00+00:00"),
    ]
    out = merge_signal_versions(rows)
    sig = out[0]
    assert sig["gated_ever"] is True
    assert sig["bettable_ever"] is True


def test_signal_history_settles_final(monkeypatch, tmp_path):
    from app.jobs.live import signal_history

    class FakeRow(dict):
        pass

    class FakeConn:
        def execute(self, sql, params=()):
            rows = [_row_json([_edge(market="total", side="over", line=220.0, ev=0.3)], created="2026-11-04T08:00:20+00:00")]
            return type("R", (), {"fetchall": staticmethod(lambda: [FakeRow(created_at=c, details=d) for c, d in rows])})()

    live_mod._SIGNAL_CACHE.clear()
    sigs = signal_history(FakeConn(), "g9", 103, 120)  # 总分 223 > 220 → over 命中
    assert len(sigs) == 1
    assert sigs[0]["result"] == "won"
    # 缓存命中但结算参数不同 → 结果随最终比分重算
    sigs2 = signal_history(FakeConn(), "g9", 100, 115)  # 215 < 220 → over 落空
    assert sigs2[0]["result"] == "lost"


def test_signal_history_no_final_keeps_unset():
    from app.jobs.live import signal_history, _SIGNAL_CACHE

    class FakeRow(dict):
        pass

    class FakeConn:
        def execute(self, sql, params=()):
            rows = [_row_json([_edge()], created="2026-11-04T08:00:20+00:00")]
            return type("R", (), {"fetchall": staticmethod(lambda: [FakeRow(created_at=c, details=d) for c, d in rows])})()

    _SIGNAL_CACHE.clear()
    sigs = signal_history(FakeConn(), "g8", None, None)
    assert "result" not in sigs[0]
    _SIGNAL_CACHE.clear()
