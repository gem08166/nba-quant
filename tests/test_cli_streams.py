"""无控制台启动（DETACHED_PROCESS / pythonw）时 stdout/stderr 为 None 的兜底。

回归背景：本机 venv 的 python.exe 被替换后，serve/watch 用分离进程启动会
「几秒后消失且无日志」——因为 uvicorn 日志和 print() 拿到的是 None。
"""
import sys

from app import cli


def test_ensure_std_streams_fills_missing(monkeypatch):
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    cli._ensure_std_streams()

    assert sys.stdout is not None
    assert sys.stderr is not None
    # 写一下确认真的可用（指向 devnull）
    sys.stdout.write("ok")
    sys.stdout.flush()


def test_ensure_std_streams_keeps_existing(monkeypatch):
    marker = object()
    monkeypatch.setattr(sys, "stdout", marker)

    cli._ensure_std_streams()

    assert sys.stdout is marker
