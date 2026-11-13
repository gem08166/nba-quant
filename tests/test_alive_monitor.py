"""alive_monitor（本地存活检测 + ntfy 推送）单元测试。"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from scripts import alive_monitor as am


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(am, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(am, "LOG_PATH", tmp_path / "alive.log")
    return tmp_path


def _all_ok(monkeypatch):
    monkeypatch.setattr(am, "check_serve", lambda: (True, "ok"))
    monkeypatch.setattr(am, "check_watch", lambda: (True, "process found"))
    monkeypatch.setattr(am, "check_cl", lambda: (True, "outside window"))


def _capture_pushes(monkeypatch):
    pushes: list[tuple[str, str]] = []

    def fake_send(title, text):
        pushes.append((title, text))
        return True

    monkeypatch.setattr(am, "_ntfy_send", fake_send)
    return pushes


def test_ntfy_send_encodes_title_as_utf8(monkeypatch):
    captured = {}

    class Resp:
        status_code = 200

    def fake_post(url, content=None, headers=None, timeout=None):
        captured["url"] = url
        captured["content"] = content
        captured["headers"] = headers
        return Resp()

    monkeypatch.setattr(am, "NTFY_TOPIC", "topic-x")
    monkeypatch.setattr(am.httpx, "post", fake_post)

    assert am._ntfy_send("服务异常", "body") is True
    assert captured["content"] == "body".encode("utf-8")
    assert captured["headers"]["Title"].decode("utf-8") == "服务异常"


def test_alert_pushed_and_state_recorded(isolated, monkeypatch):
    monkeypatch.setattr(am, "check_serve", lambda: (False, "connection refused"))
    monkeypatch.setattr(am, "check_watch", lambda: (True, "ok"))
    monkeypatch.setattr(am, "check_cl", lambda: (True, "ok"))
    pushes = _capture_pushes(monkeypatch)

    am.run_once()

    assert len(pushes) == 1
    assert "服务异常" in pushes[0][0]
    assert "serve" in pushes[0][1]
    state = json.loads((isolated / "state.json").read_text(encoding="utf-8"))
    assert "serve" in state["alerted"]


def test_alert_throttled_within_window(isolated, monkeypatch):
    monkeypatch.setattr(am, "check_serve", lambda: (False, "connection refused"))
    monkeypatch.setattr(am, "check_watch", lambda: (True, "ok"))
    monkeypatch.setattr(am, "check_cl", lambda: (True, "ok"))
    pushes = _capture_pushes(monkeypatch)

    am.run_once()
    first_ts = json.loads((isolated / "state.json").read_text(encoding="utf-8"))["alerted"]["serve"]
    am.run_once()

    assert len(pushes) == 1, "第二轮的重复告警应被节流"
    state = json.loads((isolated / "state.json").read_text(encoding="utf-8"))
    assert state["alerted"]["serve"] == first_ts  # 节流时不刷新时间戳


def test_alert_repushed_after_throttle_window(isolated, monkeypatch):
    old = (datetime.now(timezone.utc) - timedelta(minutes=am.THROTTLE_MIN + 1)).isoformat()
    (isolated / "state.json").write_text(json.dumps({"alerted": {"serve": old}}), encoding="utf-8")
    monkeypatch.setattr(am, "check_serve", lambda: (False, "connection refused"))
    monkeypatch.setattr(am, "check_watch", lambda: (True, "ok"))
    monkeypatch.setattr(am, "check_cl", lambda: (True, "ok"))
    pushes = _capture_pushes(monkeypatch)

    am.run_once()

    assert len(pushes) == 1
    state = json.loads((isolated / "state.json").read_text(encoding="utf-8"))
    assert state["alerted"]["serve"] != old


def test_recovery_push_clears_alert(isolated, monkeypatch):
    (isolated / "state.json").write_text(
        json.dumps({"alerted": {"serve": datetime.now(timezone.utc).isoformat()}}),
        encoding="utf-8",
    )
    _all_ok(monkeypatch)
    pushes = _capture_pushes(monkeypatch)

    am.run_once()

    assert len(pushes) == 1
    assert "已恢复" in pushes[0][0]
    state = json.loads((isolated / "state.json").read_text(encoding="utf-8"))
    assert state["alerted"] == {}


def test_heartbeat_only_once_per_day(isolated, monkeypatch):
    _all_ok(monkeypatch)
    pushes = _capture_pushes(monkeypatch)

    am.run_once(heartbeat=True)
    am.run_once(heartbeat=True)

    assert len(pushes) == 1
    assert "心跳" in pushes[0][0]


def test_heartbeat_off_by_default(isolated, monkeypatch):
    _all_ok(monkeypatch)
    pushes = _capture_pushes(monkeypatch)

    am.run_once()

    assert pushes == []


def _make_db(tmp_path, rows):
    db = tmp_path / "nba.db"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE games (id TEXT, slate_date TEXT, status TEXT, tipoff TEXT)"
    )
    conn.executemany(
        "INSERT INTO games (id, slate_date, status, tipoff) VALUES (?,?,?,?)", rows
    )
    conn.commit()
    conn.close()
    return tmp_path


def test_check_cl_no_games(tmp_path, monkeypatch):
    _make_db(tmp_path, [])
    monkeypatch.setattr(am, "DATA_DIR", tmp_path)
    monkeypatch.setattr(am, "current_slate_date", lambda: "2099-01-01")
    monkeypatch.setattr(am, "_python_cmdlines", lambda: "")

    ok, msg = am.check_cl()

    assert ok is True
    assert "no games" in msg


def test_check_cl_window_active_but_not_running(tmp_path, monkeypatch):
    tipoff = datetime.now(timezone.utc) + timedelta(hours=1)
    _make_db(
        tmp_path,
        [("g1", "2099-01-01", "STATUS_SCHEDULED", tipoff.isoformat())],
    )
    monkeypatch.setattr(am, "DATA_DIR", tmp_path)
    monkeypatch.setattr(am, "current_slate_date", lambda: "2099-01-01")
    monkeypatch.setattr(am, "_python_cmdlines", lambda: "python.exe -m app.cli watch")

    ok, msg = am.check_cl()

    assert ok is False
    assert "cl_session not running" in msg


def test_check_cl_window_active_and_running(tmp_path, monkeypatch):
    tipoff = datetime.now(timezone.utc) + timedelta(hours=1)
    _make_db(
        tmp_path,
        [("g1", "2099-01-01", "STATUS_SCHEDULED", tipoff.isoformat())],
    )
    monkeypatch.setattr(am, "DATA_DIR", tmp_path)
    monkeypatch.setattr(am, "current_slate_date", lambda: "2099-01-01")
    monkeypatch.setattr(am, "_python_cmdlines", lambda: "python.exe -m scripts.cl_session")

    ok, msg = am.check_cl()

    assert ok is True
    assert "running in window" in msg


def test_check_cl_all_games_final_is_ok(tmp_path, monkeypatch):
    tipoff = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    _make_db(
        tmp_path,
        [
            ("g1", "2099-01-01", "STATUS_FINAL", tipoff),
            ("g2", "2099-01-01", "STATUS_FINAL", tipoff),
        ],
    )
    monkeypatch.setattr(am, "DATA_DIR", tmp_path)
    monkeypatch.setattr(am, "current_slate_date", lambda: "2099-01-01")
    monkeypatch.setattr(am, "_python_cmdlines", lambda: "")

    ok, msg = am.check_cl()

    assert ok is True
    assert "all games final" in msg


def test_check_cl_process_list_unavailable_is_treated_as_ok(tmp_path, monkeypatch):
    tipoff = datetime.now(timezone.utc) + timedelta(hours=1)
    _make_db(
        tmp_path,
        [("g1", "2099-01-01", "STATUS_SCHEDULED", tipoff.isoformat())],
    )
    monkeypatch.setattr(am, "DATA_DIR", tmp_path)
    monkeypatch.setattr(am, "current_slate_date", lambda: "2099-01-01")
    monkeypatch.setattr(am, "_python_cmdlines", lambda: None)

    ok, _ = am.check_cl()

    assert ok is True


def test_check_serve_handles_bad_status(monkeypatch):
    class Resp:
        status_code = 500

        def json(self):
            return {"ok": False}

    monkeypatch.setattr(am.httpx, "get", lambda *a, **k: Resp())

    ok, msg = am.check_serve()

    assert ok is False
    assert "500" in msg
