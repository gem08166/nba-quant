"""以 CREATE_BREAKAWAY_FROM_JOB 标志启动脱离进程（脱离宿主 Job Object）。

为什么需要：OpenCode/IDE 这类宿主会给子进程分配 Job Object，并设置
KILL_ON_JOB_CLOSE——宿主 shell 一结束，所有后代进程被连带杀掉，
导致 serve / watch 这些"常驻服务"莫名其妙消失。
Windows 上普通方法（Start-Process / WMI Create）都无法避开，
只有显式传 CREATE_BREAKAWAY_FROM_JOB 才能真正脱离。

若当前进程所在 Job 不允许 breakaway（有些 Job 设了
BREAKAWAY_OK / SILENT_BREAKAWAY_OK 之外的限制），CreateProcess 会失败，
此时退回普通创建（仍比什么都不做���）。

用法：
    python scripts/launch_detached.py <executable> [args...]
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
from ctypes import wintypes

CREATE_BREAKAWAY_FROM_JOB = 0x01000000
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_NO_WINDOW = 0x08000000
DETACHED_PROCESS = 0x00000008

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)


class STARTUPINFO(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("lpReserved", wintypes.LPWSTR),
        ("lpDesktop", wintypes.LPWSTR),
        ("lpTitle", wintypes.LPWSTR),
        ("dwX", wintypes.DWORD),
        ("dwY", wintypes.DWORD),
        ("dwXSize", wintypes.DWORD),
        ("dwYSize", wintypes.DWORD),
        ("dwXCountChars", wintypes.DWORD),
        ("dwYCountChars", wintypes.DWORD),
        ("dwFillAttribute", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("wShowWindow", wintypes.WORD),
        ("cbReserved2", wintypes.WORD),
        ("lpReserved2", ctypes.POINTER(ctypes.c_byte)),
        ("hStdInput", wintypes.HANDLE),
        ("hStdOutput", wintypes.HANDLE),
        ("hStdError", wintypes.HANDLE),
    ]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("hProcess", wintypes.HANDLE),
        ("hThread", wintypes.HANDLE),
        ("dwProcessId", wintypes.DWORD),
        ("dwThreadId", wintypes.DWORD),
    ]


def create_detached(exe: str, args: list[str], cwd: str | None, log_path: str | None = None) -> int:
    """返回新进程 PID；失败抛 OSError。

    注意：不通过 STARTF_USESTDHANDLES 传句柄——Windows 上 bInheritHandles=True
    时所有句柄都必须可继承，Python 的文件句柄默认不可继承，硬传会得到
    ERROR_INVALID_HANDLE(6)。需要日志就让子进程自己打开文件。
    """
    cmdline = subprocess.list2cmdline([exe, *args])
    si = STARTUPINFO()
    si.cb = ctypes.sizeof(si)
    pi = PROCESS_INFORMATION()

    flags = (
        CREATE_BREAKAWAY_FROM_JOB
        | CREATE_NEW_PROCESS_GROUP
        | DETACHED_PROCESS
        | CREATE_NO_WINDOW
    )
    ok = kernel32.CreateProcessW(
        None,
        ctypes.create_unicode_buffer(cmdline),
        None,
        None,
        False,
        flags,
        None,
        cwd,
        ctypes.byref(si),
        ctypes.byref(pi),
    )
    if not ok:
        err = ctypes.get_last_error()
        raise OSError(err, f"CreateProcessW failed (err={err})")
    kernel32.CloseHandle(pi.hThread)
    kernel32.CloseHandle(pi.hProcess)
    return pi.dwProcessId


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: launch_detached.py <exe> [args...] [--log PATH]", file=sys.stderr)
        raise SystemExit(2)
    argv = sys.argv[1:]
    log_path = None
    if "--log" in argv:
        idx = argv.index("--log")
        log_path = argv[idx + 1]
        del argv[idx : idx + 2]
    exe = argv[0]
    args = argv[1:]
    cwd = os.getcwd()
    try:
        pid = create_detached(exe, args, cwd, log_path)
        print(pid)
    except OSError as exc:
        print(f"breakaway failed ({exc}); falling back", file=sys.stderr)
        proc = subprocess.Popen(
            [exe, *args],
            cwd=cwd,
            creationflags=CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS,
            close_fds=True,
        )
        print(proc.pid)


if __name__ == "__main__":
    main()
