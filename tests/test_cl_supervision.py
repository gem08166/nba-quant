"""CL 会话自动拉起 / 单实例锁 / S4U 探测健壮性测试。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    from scripts import alive_monitor as am

    monkeypatch.setattr(am, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(am, "LOG_PATH", tmp_path / "alive.log")
    return tmp_path


def _all_ok(monkeypatch, am):
    monkeypatch.setattr(am, "check_serve", lambda: (True, "ok"))
    monkeypatch.setattr(am, "check_watch", lambda: (True, "ok"))
    monkeypatch.setattr(am, "check_guards", lambda: (True, "ok"))


def _capture_pushes(monkeypatch, am):
    pushes = []

    def fake_send(title, text):
        pushes.append((title, text))
        return True

    monkeypatch.setattr(am, "_ntfy_send", fake_send)
    return pushes


def test_cl_down_triggers_relaunch(isolated, monkeypatch):
    from scripts import alive_monitor as am

    _all_ok(monkeypatch, am)
    calls = {"n": 0}

    def fake_check_cl():
        calls["n"] += 1
        return (calls["n"] > 1, "window active")

    monkeypatch.setattr(am, "check_cl", fake_check_cl)
    monkeypatch.setattr(am, "_relaunch_cl", lambda: 12345)
    monkeypatch.setattr(am.time, "sleep", lambda _s: None)
    pushes = _capture_pushes(monkeypatch, am)

    am.run_once()

    assert calls["n"] >= 2
    assert any("自动重启" in title for title, _ in pushes)
    state = json.loads((isolated / "state.json").read_text(encoding="utf-8"))
    assert "cl_relaunch" in state
    assert state["alerted"] == {}


def test_cl_relaunch_respects_cooldown(isolated, monkeypatch):
    from scripts import alive_monitor as am

    recent = datetime.now(timezone.utc).isoformat()
    (isolated / "state.json").write_text(
        json.dumps({"alerted": {}, "cl_relaunch": recent}), encoding="utf-8"
    )
    _all_ok(monkeypatch, am)
    monkeypatch.setattr(am, "check_cl", lambda: (False, "window active"))
    relaunched = []
    monkeypatch.setattr(am, "_relaunch_cl", lambda: relaunched.append(1) or 999)
    _capture_pushes(monkeypatch, am)

    am.run_once()

    assert relaunched == [], "冷却期内不应重复拉起"


def test_cl_relaunch_failure_keeps_alert(isolated, monkeypatch):
    from scripts import alive_monitor as am

    _all_ok(monkeypatch, am)
    monkeypatch.setattr(am, "check_cl", lambda: (False, "window active but not running"))
    monkeypatch.setattr(am, "_relaunch_cl", lambda: None)
    pushes = _capture_pushes(monkeypatch, am)

    am.run_once()

    assert any("服务异常" in title for title, _ in pushes)


def test_cl_session_single_instance_lock(tmp_path, monkeypatch):
    from scripts import cl_session

    monkeypatch.setattr(cl_session, "DATA_DIR", tmp_path)

    first = cl_session.acquire_single_instance_lock()
    assert first is not None
    second = cl_session.acquire_single_instance_lock()
    assert second is None, "第二个实例必须被锁挡住"

    first.close()
    third = cl_session.acquire_single_instance_lock()
    assert third is not None, "锁释放后应能重新获取"
    third.close()


def test_cl_connect_waits_for_writers(tmp_path, monkeypatch):
    """CL 的连接必须带 30s 忙等（默认 5s，watch 一写就 locked）。"""
    from scripts import cl_session

    monkeypatch.setattr(cl_session, "DATA_DIR", tmp_path)
    conn = cl_session._connect()
    try:
        assert conn.execute("PRAGMA busy_timeout").fetchone()[0] == 30000
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    finally:
        conn.close()


def test_final_closing_captures_and_settles(monkeypatch):
    """安全阀退出前也要补一次收盘 + 结算，不能空手走。"""
    from scripts import cl_session

    calls = []
    monkeypatch.setattr(cl_session, "capture_closing", lambda conn: calls.append("capture") or [])
    monkeypatch.setattr(
        cl_session,
        "settle",
        lambda conn: calls.append("settle")
        or [{"bet_id": 1, "result": "won", "profit": 1.5}],
    )
    logged = []
    monkeypatch.setattr(cl_session, "_log", lambda msg: logged.append(msg))

    import sqlite3

    cl_session._final_closing(sqlite3.connect(":memory:"), "收尾")

    assert calls == ["capture", "settle"]
    assert any("收尾结算 #1" in msg for msg in logged)


def test_boot_start_launches_cl_in_window(monkeypatch):
    from scripts import boot_start

    monkeypatch.setattr(boot_start, "TARGETS", [])
    monkeypatch.setattr(boot_start.cl_window, "window_state", lambda: ("active", "window 06:00~12:30"))
    monkeypatch.setattr(
        boot_start,
        "_running",
        lambda needle: not needle.startswith("scripts.cl_session"),
    )
    launched = []

    def fake_launch(module, extra=None):
        launched.append((module, extra))
        return 4321

    monkeypatch.setattr(boot_start, "_launch", fake_launch)
    monkeypatch.setattr(boot_start.time, "sleep", lambda _s: None)
    monkeypatch.setattr(boot_start, "log", lambda _m: None)
    monkeypatch.setattr(boot_start.sys, "argv", ["boot_start"])

    boot_start.main()

    assert launched, "窗口内应拉起 cl_session"
    assert launched[0][0] == "scripts.cl_session"


def test_boot_start_skips_cl_outside_window(monkeypatch):
    from scripts import boot_start

    monkeypatch.setattr(boot_start, "TARGETS", [])
    monkeypatch.setattr(boot_start.cl_window, "window_state", lambda: ("before", "before window"))
    monkeypatch.setattr(boot_start, "_running", lambda _needle: False)
    launched = []
    monkeypatch.setattr(boot_start, "_launch", lambda module, extra=None: launched.append(module) or 1)
    monkeypatch.setattr(boot_start.time, "sleep", lambda _s: None)
    monkeypatch.setattr(boot_start, "log", lambda _m: None)
    monkeypatch.setattr(boot_start.sys, "argv", ["boot_start"])

    boot_start.main()

    assert "scripts.cl_session" not in launched
