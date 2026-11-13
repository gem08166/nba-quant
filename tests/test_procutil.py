"""进程探测 / 守护自启相关测试（procutil、boot_start、pythonw 兼容性）。"""

from __future__ import annotations

import importlib
import sys

import pytest

from scripts import procutil


def test_has_process_none_is_unknown():
    assert procutil.has_process(None, "app.cli watch") is None


def test_has_process_normalizes_whitespace():
    raw = '"C:\\py\\pythonw.exe"  -m   app.cli   watch\n'
    assert procutil.has_process(raw, "app.cli watch") is True


def test_has_process_false_when_absent():
    assert procutil.has_process("python.exe -m something_else", "app.cli watch") is False


def test_python_cmdlines_prefers_cim_output(monkeypatch):
    class R:
        stdout = "python.exe -m app.cli serve\r\n"

    monkeypatch.setattr(procutil.subprocess, "run", lambda *a, **k: R())
    assert "app.cli serve" in procutil.python_cmdlines()


def test_python_cmdlines_degrades_to_none_when_probe_unavailable(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no shell available")

    monkeypatch.setattr(procutil.subprocess, "run", boom)
    assert procutil.python_cmdlines() is None


def test_python_cmdlines_empty_when_no_python_processes(monkeypatch):
    class Empty:
        stdout = ""

    # PowerShell CIM 无进程时返回空，wmic/tasklist 同样为空
    monkeypatch.setattr(procutil.subprocess, "run", lambda *a, **k: Empty())
    assert procutil.python_cmdlines() == ""


def test_guards_import_under_pythonw(monkeypatch):
    """pythonw 下 sys.stdout/stderr 是 None，守护模块导入不能崩（曾因此秒退）。"""
    import scripts.serve_watchdog as sw
    import scripts.watch_guard as wg

    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    importlib.reload(sw)
    importlib.reload(wg)

    assert sw.INTERVAL > 0
    assert wg.INTERVAL > 0


def test_boot_start_returns_unknown_when_probe_unavailable(monkeypatch):
    from scripts import boot_start

    monkeypatch.setattr(boot_start.procutil, "python_cmdlines", lambda: None)
    assert boot_start._running("scripts.watch_guard") is None


def test_boot_start_detects_running_guard(monkeypatch):
    from scripts import boot_start

    monkeypatch.setattr(
        boot_start.procutil,
        "python_cmdlines",
        lambda: 'pythonw.exe  -m  scripts.serve_watchdog\n',
    )
    assert boot_start._running("scripts.serve_watchdog") is True
    assert boot_start._running("scripts.watch_guard") is False


def test_check_guards_all_present(monkeypatch):
    from scripts import alive_monitor as am

    monkeypatch.setattr(
        am,
        "_python_cmdlines",
        lambda: "a -m scripts.serve_watchdog\nb -m scripts.watch_guard\n",
    )
    ok, msg = am.check_guards()
    assert ok is True
    assert "both" in msg


def test_check_guards_reports_missing(monkeypatch):
    from scripts import alive_monitor as am

    monkeypatch.setattr(am, "_python_cmdlines", lambda: "a -m app.cli watch\n")
    ok, msg = am.check_guards()
    assert ok is False
    assert "serve_watchdog" in msg and "watch_guard" in msg


def test_check_guards_unknown_probe_is_ok(monkeypatch):
    from scripts import alive_monitor as am

    monkeypatch.setattr(am, "_python_cmdlines", lambda: None)
    ok, _ = am.check_guards()
    assert ok is True
