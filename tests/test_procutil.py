"""进程探测（procutil）/ 守护自启 / pythonw 兼容性测试。"""

from __future__ import annotations

import importlib
import sys

import pytest

from scripts import procutil


class _Result:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def _patch_run(monkeypatch, *, ps=None, wmic=None, tasklist=None, ps_seq=None):
    """按可执行文件名分发假结果；ps_seq 可给出多次 PowerShell 调用序列。"""
    seq = list(ps_seq or [])

    def fake_run(cmd, **kwargs):
        exe = str(cmd[0]).lower()
        if "powershell" in exe:
            if seq:
                return seq.pop(0)
            return ps
        if "wmic" in exe:
            return wmic
        return tasklist

    monkeypatch.setattr(procutil.subprocess, "run", fake_run)
    monkeypatch.setattr(procutil.time, "sleep", lambda _s: None)


TASKLIST_HAS_PY = _Result(0, "python.exe   1234 Console   1   10,000 K\r\n")
TASKLIST_EMPTY = _Result(0, "INFO: No tasks are running which match the specified criteria.\r\n")


def test_has_process_none_is_unknown():
    assert procutil.has_process(None, "app.cli watch") is None


def test_has_process_normalizes_whitespace():
    raw = '"C:\\py\\pythonw.exe"  -m   app.cli   watch\n'
    assert procutil.has_process(raw, "app.cli watch") is True


def test_has_process_false_when_absent():
    assert procutil.has_process("python.exe -m something_else", "app.cli watch") is False


def test_python_cmdlines_prefers_cim_output(monkeypatch):
    _patch_run(monkeypatch, ps=_Result(0, "python.exe -m app.cli serve\r\n"))
    assert "app.cli serve" in procutil.python_cmdlines()


def test_python_cmdlines_empty_when_no_python_processes(monkeypatch):
    _patch_run(
        monkeypatch,
        ps=_Result(0, ""),
        wmic=_Result(1, ""),
        tasklist=TASKLIST_EMPTY,
    )
    assert procutil.python_cmdlines() == ""


def test_python_cmdlines_degrades_to_none_when_probe_unavailable(monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("no shell available")

    monkeypatch.setattr(procutil.subprocess, "run", boom)
    monkeypatch.setattr(procutil.time, "sleep", lambda _s: None)
    assert procutil.python_cmdlines() is None


def test_powershell_failure_is_unknown_not_missing(monkeypatch):
    """WMI 拒绝访问时绝不能返回"没有进程"——否则看门狗会重复拉起 watch。"""
    _patch_run(
        monkeypatch,
        ps=_Result(1, "Get-CimInstance : Access is denied.\r\nAt line:1 char:1", ""),
        wmic=_Result(1, ""),
        tasklist=TASKLIST_HAS_PY,
    )
    assert procutil.python_cmdlines() is None


def test_rc0_but_error_marker_is_unknown(monkeypatch):
    _patch_run(
        monkeypatch,
        ps=_Result(0, "Get-CimInstance : 拒绝访问。\r\n", ""),
        wmic=_Result(1, ""),
        tasklist=TASKLIST_HAS_PY,
    )
    assert procutil.python_cmdlines() is None


def test_stderr_text_is_treated_as_unknown(monkeypatch):
    _patch_run(
        monkeypatch,
        ps=_Result(0, "python.exe -m app.cli watch\r\n", "warning: x"),
        wmic=_Result(1, ""),
        tasklist=TASKLIST_HAS_PY,
    )
    assert procutil.python_cmdlines() is None


def test_retry_succeeds_on_second_attempt(monkeypatch):
    _patch_run(
        monkeypatch,
        ps_seq=[_Result(1, "boom", ""), _Result(0, "python.exe -m app.cli watch\r\n")],
    )
    assert "app.cli watch" in procutil.python_cmdlines()


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
