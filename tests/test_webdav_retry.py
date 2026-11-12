"""WebDAV 客户端：瞬时网络错误重试（坚果云偶发 read timeout）。"""
import httpx
import pytest

from app.jobs import webdav


def test_retry_succeeds_after_transient_timeout(monkeypatch):
    monkeypatch.setattr(webdav.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise httpx.ReadTimeout("temporary")
        return "ok"

    assert webdav._retry(flaky) == "ok"
    assert calls["n"] == 3


def test_retry_reraises_after_attempts(monkeypatch):
    monkeypatch.setattr(webdav.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def always_timeout():
        calls["n"] += 1
        raise httpx.ReadTimeout("still down")

    with pytest.raises(httpx.ReadTimeout):
        webdav._retry(always_timeout, attempts=3)
    assert calls["n"] == 3


def test_retry_does_not_swallow_http_status_errors(monkeypatch):
    monkeypatch.setattr(webdav.time, "sleep", lambda *_: None)
    calls = {"n": 0}

    def not_found():
        calls["n"] += 1
        resp = httpx.Response(500, request=httpx.Request("GET", "https://example.test/x"))
        resp.raise_for_status()

    with pytest.raises(httpx.HTTPStatusError):
        webdav._retry(not_found)
    assert calls["n"] == 1  # 状态码错误不重试
