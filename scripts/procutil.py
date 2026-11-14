"""进程探测工具：跨 Windows 版本的 python 进程命令行枚举。

背景与教训：
- Windows 11 24H2 起 ``wmic`` 已被移除，旧脚本用它判断 serve/watch 是否在跑，
  会永远拿不到结果。
- 只用 PowerShell CIM 也不够可靠：WMI 偶发拒绝访问/超时会往 stdout 里吐出
  **错误文本**，调用方会把它当成"没有这个进程"，于是看门狗误判为挂了 →
  重复拉起 watch → 重复抓数、重复烧 API 额度。2026-11-14 的日志里就是这样
  （07:41/07:43/08:09/08:20 出现莫名重启）。

因此这里的约定是：
- 返回 ``str``：可信的命令行文本（空串 = 确实没有 python 进程）
- 返回 ``None``：**探测不可用**（命令失败/输出可疑），调用方必须保守处理，
  绝不能据此判定"进程不存在"。
"""
from __future__ import annotations

import subprocess
import time

_PS_CMD = (
    "$ErrorActionPreference='Stop';"
    "Get-CimInstance Win32_Process -Filter \"Name='python.exe' or Name='pythonw.exe'\" "
    "| Select-Object -ExpandProperty CommandLine"
)

# 出现在 stdout 里就说明 PowerShell/WMI 其实报错了（不同语言版本）
_ERROR_MARKERS = (
    "Get-CimInstance",
    "Access is denied",
    "拒绝访问",
    "At line:",
    "所在位置",
    "无法",
    "Exception",
)


def _looks_like_error(text: str) -> bool:
    return any(marker in text for marker in _ERROR_MARKERS)


def _run(cmd: list[str], timeout: int):
    """执行命令，返回 (returncode, stdout, stderr)；失败返回 None。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            encoding="utf-8",
            errors="replace",
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def _ps_cmdlines(timeout: int) -> str | None:
    res = _run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_CMD],
        timeout,
    )
    if res is None:
        return None
    rc, out, err = res
    if rc != 0 or err.strip():
        return None
    if _looks_like_error(out):
        return None
    return out


def _wmic_cmdlines() -> str | None:
    res = _run(
        ["wmic", "process", "where", "name='python.exe' or name='pythonw.exe'",
         "get", "CommandLine", "/value"],
        20,
    )
    if res is None:
        return None
    rc, out, err = res
    if rc != 0 or err.strip() or _looks_like_error(out):
        return None
    return out.replace("app.cli  ", "app.cli ")


def _tasklist_says_python() -> bool | None:
    res = _run(["tasklist", "/FI", "IMAGENAME eq python.exe", "/NH"], 15)
    if res is None:
        return None
    _, out, _ = res
    return "python.exe" in out or "pythonw.exe" in out


def python_cmdlines(timeout: int = 20, retries: int = 2) -> str | None:
    """返回所有 python/pythonw 进程的命令行（换行分隔）。

    None = 探测不可用（调用方不得据此判定进程不存在）。
    """
    for attempt in range(retries):
        out = _ps_cmdlines(timeout)
        if out is not None:
            return out
        if attempt + 1 < retries:
            time.sleep(1.5)  # WMI 刚启动/刚从睡眠恢复时经常第一次失败

    out = _wmic_cmdlines()
    if out is not None:
        return out

    has_python = _tasklist_says_python()
    if has_python is None:
        return None
    if has_python:
        return None  # 有 python 进程但拿不到命令行 → 未知，不要误判
    return ""


def has_process(cmdlines: str | None, needle: str) -> bool | None:
    """判断命令行里是否含 needle；探测不可用返回 None（未知）。"""
    if cmdlines is None:
        return None
    return needle in " ".join(cmdlines.split())
