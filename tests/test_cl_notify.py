"""CL 会话开始/结束 ntfy 通知（0.90）单元测试。"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts import cl_session


@pytest.fixture()
def posted(monkeypatch):
    """捕获 httpx.post 调用，返回 (calls, setter)。"""
    calls: list[tuple[str, dict]] = []

    class _Resp:
        status_code = 200

    def fake_post(url, **kw):
        calls.append((url, kw))
        return _Resp()

    monkeypatch.setattr(cl_session.httpx, "post", fake_post)
    return calls


def test_notify_disabled_without_topic(monkeypatch, posted):
    monkeypatch.setattr(cl_session, "NTFY_TOPIC", "")
    assert cl_session._notify("标题", "正文") is False
    assert posted == []  # 未配置 topic 不应发请求


def test_notify_posts_to_ntfy(monkeypatch, posted):
    monkeypatch.setattr(cl_session, "NTFY_TOPIC", "topic-x")
    ok = cl_session._notify("CL 开始捕捉", "2026-11-15：5 场")
    assert ok is True
    url, kw = posted[0]
    assert url.endswith("/topic-x")
    assert kw["headers"]["Title"] == "CL 开始捕捉".encode("utf-8")
    assert "5 场" in kw["content"].decode("utf-8")


def test_notify_swallows_errors(monkeypatch):
    """网络挂了也不能影响捕捉主流程。"""

    def boom(*a, **kw):
        raise RuntimeError("net down")

    monkeypatch.setattr(cl_session, "NTFY_TOPIC", "topic-x")
    monkeypatch.setattr(cl_session.httpx, "post", boom)
    assert cl_session._notify("t", "x") is False


def test_final_closing_returns_profit_sum(tmp_path, monkeypatch):
    """_final_closing 返回结算盈亏合计（供结束通知）。"""
    from app import config as cfg

    monkeypatch.setattr(cfg, "DATA_DIR", tmp_path)
    monkeypatch.setattr(cl_session, "DATA_DIR", tmp_path)

    class _FakeConn:
        def commit(self):
            pass

    monkeypatch.setattr(
        cl_session, "capture_closing", lambda conn: [{"game_id": "g1"}]
    )
    monkeypatch.setattr(
        cl_session,
        "settle",
        lambda conn: [
            {"bet_id": 1, "result": "win", "profit": 8.8},
            {"bet_id": 2, "result": "loss", "profit": -2.0},
        ],
    )
    profit = cl_session._final_closing(_FakeConn(), "测试")
    assert profit == pytest.approx(6.8)
