"""workflow_dispatch 触发器：没有令牌时不发请求，204 视为成功。"""
from scripts import trigger_refresh


class _Resp:
    status_code = 204
    text = ""


def test_no_token_returns_not_ok(monkeypatch):
    monkeypatch.setattr(trigger_refresh, "token", lambda: "")
    result = trigger_refresh.trigger_workflow()
    assert result["ok"] is False
    assert "令牌" in result["message"] or "GH_TOKEN" in result["message"]


def test_dispatch_posts_ref_and_auth(monkeypatch):
    monkeypatch.setattr(trigger_refresh, "token", lambda: "tok-1")
    seen = {}

    def fake_post(url, headers=None, json=None, timeout=None):
        seen.update(url=url, ref=json["ref"], auth=headers["Authorization"])
        return _Resp()

    monkeypatch.setattr(trigger_refresh.httpx, "post", fake_post)
    result = trigger_refresh.trigger_workflow("odds-refresh.yml")

    assert result["ok"] is True
    assert seen["ref"] == "main"
    assert seen["auth"] == "Bearer tok-1"
    assert seen["url"].endswith("/actions/workflows/odds-refresh.yml/dispatches")


def test_dispatch_reports_bad_token(monkeypatch):
    monkeypatch.setattr(trigger_refresh, "token", lambda: "tok-1")

    class _Bad(_Resp):
        status_code = 401
        text = '{"message":"Bad credentials"}'

    monkeypatch.setattr(trigger_refresh.httpx, "post", lambda *a, **k: _Bad())
    result = trigger_refresh.trigger_workflow()

    assert result["ok"] is False
    assert "权限" in result["message"] or "无效" in result["message"]
