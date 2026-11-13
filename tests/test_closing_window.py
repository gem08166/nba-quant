"""收盘捕获窗口的回归测试（CI 定时任务抖动兜底）。

背景：GitHub Actions 的 cron 实测约 1 小时一跳，原来 120 分钟的收盘窗口
只有 2 次捕捉机会，一旦某个 tick 被跳过就会出现「开球前 2 小时没有快照」的
缺口（11-06 那晚 7 场）。窗口放宽到 180 分钟后应有 3 次机会。
"""
import sqlite3
from datetime import datetime, timedelta, timezone

from app.jobs import settle


def _make_db(path):
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE games (
          id TEXT PRIMARY KEY, slate_date TEXT, tipoff TEXT, status TEXT
        );
        """
    )
    now = datetime.now(timezone.utc)

    def tip(minutes):
        return (now + timedelta(minutes=minutes)).isoformat(timespec="seconds")

    rows = [
        ("near", tip(30), "STATUS_SCHEDULED"),      # 窗口内
        ("edge", tip(170), "STATUS_SCHEDULED"),     # 窗口内（180 分钟放宽后）
        ("far", tip(200), "STATUS_SCHEDULED"),      # 窗口外
        ("started", tip(-10), "STATUS_SCHEDULED"),  # 已开赛
        ("final", tip(60), "STATUS_FINAL"),         # 已完赛
    ]
    conn.executemany(
        "INSERT INTO games(id, slate_date, tipoff, status) VALUES(?, '2026-11-13', ?, ?)",
        rows,
    )
    conn.commit()
    return conn


def test_capture_closing_selects_only_games_inside_window(monkeypatch, tmp_path):
    conn = _make_db(tmp_path / "nba.db")
    captured = []

    def fake_refresh(_conn, game_id, _slate):
        captured.append(game_id)
        return 1

    monkeypatch.setattr(settle.service, "refresh_game_odds", fake_refresh)
    monkeypatch.setattr(settle, "CLOSING_WINDOW_MIN", 180.0)

    items = settle.capture_closing(conn)
    conn.close()

    assert [i["game_id"] for i in items] == ["near", "edge"]
    assert captured == ["near", "edge"]


def test_capture_closing_window_is_at_least_180(monkeypatch, tmp_path):
    """默认窗口必须覆盖 3 个「小时级」tick，避免 CI 抖动造成收盘缺口。"""
    assert settle.CLOSING_WINDOW_MIN >= 180.0
