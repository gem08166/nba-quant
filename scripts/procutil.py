"""进程探测工具：跨 Windows 版本的 python 进程命令行枚举。

背景：Windows 11 24H2 起 ``wmic`` 已被移除，旧脚本用 wmic 判断
"serve/watch 是否在跑"会一直拿不到结果。这里统一走 PowerShell CIM，
wmic 只作老系统 fallback，再退化为 tasklist 只判进程是否存在。

约定（调用方必须遵守）：
- 返回 ``str``：正常的命令行文本（可能为空字符串 = 没有任何 python 进程）
- 返回 ``None``：探测能力不可用，调用方应保守视为"存活"，不要误报/误杀
"""
from __future__ import annotations

import subprocess

_PS_CMD = (
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" "
    "| Select-Object -ExpandProperty CommandLine"
)


def python_cmdlines(timeout: int = 40) -> str | None:
    """返回所有 python/pythonw 进程的命令行（换行分隔）。

    None 表示探测失败；空串表示确实没有 python 进程。
    """
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_CMD],
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        ).stdout
        if out and out.strip():
            return out
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        out = subprocess.run(
            ["wmic", "process", "where", "name='python.exe' or name='pythonw.exe'",
             "get", "CommandLine", "/value"],
            capture_output=True, text=True, timeout=25, encoding="utf-8", errors="replace",
        ).stdout or ""
        if out.strip():
            return out.replace("app.cli  ", "app.cli ")
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        alt = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq python.exe", "/NH"],
            capture_output=True, text=True, timeout=20, encoding="utf-8", errors="replace",
        ).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return None
    if "python.exe" in alt or "pythonw.exe" in alt:
        return None
    return ""


def has_process(cmdlines: str | None, needle: str) -> bool | None:
    """判断命令行里是否含 needle；探测不可用返回 None（未知）。"""
    if cmdlines is None:
        return None
    return needle in " ".join(cmdlines.split())
