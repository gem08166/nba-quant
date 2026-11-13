"""脚本日志在 pythonw（无控制台）下也必须落盘、不能抛异常。"""

from __future__ import annotations

import sys


def test_cl_session_log_writes_file_without_stdout(tmp_path, monkeypatch):
    from scripts import cl_session

    monkeypatch.setattr(cl_session, "DATA_DIR", tmp_path)
    monkeypatch.setattr(sys, "stdout", None)

    cl_session._log("测试一行")  # 不应抛异常

    files = list((tmp_path / "cl_logs").glob("cl_session_*.log"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "测试一行" in content


def test_alive_monitor_log_writes_file_without_stdout(tmp_path, monkeypatch):
    from scripts import alive_monitor

    monkeypatch.setattr(alive_monitor, "LOG_PATH", tmp_path / "alive.log")
    monkeypatch.setattr(sys, "stdout", None)

    alive_monitor.log("存活检查")

    content = (tmp_path / "alive.log").read_text(encoding="utf-8")
    assert "存活检查" in content


def test_boot_start_log_writes_file_without_stdout(tmp_path, monkeypatch):
    from scripts import boot_start

    monkeypatch.setattr(boot_start, "LOG", tmp_path / "boot.log")
    monkeypatch.setattr(sys, "stdout", None)

    boot_start.log("自启")

    content = (tmp_path / "boot.log").read_text(encoding="utf-8")
    assert "自启" in content
