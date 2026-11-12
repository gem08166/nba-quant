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


def test_interval_falls_back_to_db_when_state_missing(monkeypatch):
    """CI 每轮都是新容器：state 文件不存在时要能从库里推断上次同步时间。

    报价没变化时 insert_odds 只推进 last_seen_at（captured_at 不变），
    所以兜底必须读 COALESCE(last_seen_at, captured_at)。
    """
    from datetime import datetime, timedelta, timezone as tz

    now = datetime.now(tz.utc)
    monkeypatch.setattr(hoops, "_load_state", lambda: {})  # state 丢失
    monkeypatch.setattr(hoops, "MIN_INTERVAL_S", 150 * 60)

    seen = []

    class _Conn:
        def execute(self, sql, params=()):
            seen.append(sql)
            return self

        def fetchone(self):
            return (now - timedelta(minutes=40)).isoformat(timespec="seconds"),

        def commit(self):
            pass

    def boom(*_a, **_k):
        raise AssertionError("间隔内不应发起网络请求")

    monkeypatch.setattr(hoops, "fetch_nba_games", boom)

    stats = hoops.sync_games(conn=_Conn())
    assert "skipped" in stats, stats
    assert any("last_seen_at" in s for s in seen)


def test_interval_allows_sync_when_db_says_long_ago(monkeypatch):
    from datetime import datetime, timedelta, timezone as tz

    old = (datetime.now(tz.utc) - timedelta(hours=6)).isoformat(timespec="seconds")
    saved = {}
    monkeypatch.setattr(hoops, "_load_state", lambda: {})
    monkeypatch.setattr(hoops, "_save_state", lambda s: saved.update(s))
    monkeypatch.setattr(hoops, "MIN_INTERVAL_S", 150 * 60)
    monkeypatch.setattr(hoops, "_sleep", lambda: None)
    monkeypatch.setattr(hoops, "fetch_nba_games", lambda client: [])

    class _Conn:
        def execute(self, sql, params=()):
            return self

        def fetchone(self):
            return (old,)

        def commit(self):
            pass

    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(hoops.httpx, "Client", lambda **kw: _Client())

    stats = hoops.sync_games(conn=_Conn())
    assert "skipped" not in stats
    assert "last_sync_ts" in saved
