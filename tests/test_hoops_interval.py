"""HoopsAPI 最小调用间隔：watch 每 10 分钟一轮不能把 10 次/24h 预算打光。"""
import time

from app.ingest import hoops


def test_sync_games_skips_within_interval(monkeypatch, tmp_path):
    state = {"last_sync_ts": time.time() - 600}  # 10 分钟前刚同步过
    monkeypatch.setattr(hoops, "_load_state", lambda: dict(state))
    monkeypatch.setattr(hoops, "_save_state", lambda s: None)
    monkeypatch.setattr(hoops, "MIN_INTERVAL_S", 150 * 60)

    def boom(*_a, **_k):
        raise AssertionError("不应发起网络请求")

    monkeypatch.setattr(hoops, "fetch_nba_games", boom)

    stats = hoops.sync_games(conn=None)
    assert stats["games"] == 0
    assert "skipped" in stats


def test_sync_games_runs_after_interval(monkeypatch):
    state = {"last_sync_ts": time.time() - 200 * 60}  # 超过 150 分钟
    saved = {}

    monkeypatch.setattr(hoops, "_load_state", lambda: dict(state))
    monkeypatch.setattr(hoops, "_save_state", lambda s: saved.update(s))
    monkeypatch.setattr(hoops, "MIN_INTERVAL_S", 150 * 60)
    monkeypatch.setattr(hoops, "_sleep", lambda: None)
    monkeypatch.setattr(hoops, "fetch_nba_games", lambda client: [])

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(hoops.httpx, "Client", lambda **kw: _Client())

    class _Conn:
        def commit(self):
            pass

    stats = hoops.sync_games(conn=_Conn())
    assert stats["games"] == 0 and "skipped" not in stats
    assert "last_sync_ts" in saved
