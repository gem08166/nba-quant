"""serve 看门狗：serve 进程消失就自动拉起。

为什么需要：serve 用 pythonw 常驻（无控制台），偶尔会因外部原因消失；
没有看门狗就只能手动重启。用 pythonw 跑本脚本可获得最长的存活时间
（powershell.exe 在某些宿主下会被连带结束）。

用法（由 scripts/serve-watchdog.ps1 或 Startup VBS 拉起）：
    pythonw -m scripts.serve_watchdog
日志：data/serve-watchdog.log
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "data" / "serve-watchdog.log"
INTERVAL = 60

# pythonw 下 sys.stdout 是 None（无控制台），reconfigure 会直接抛异常把看门狗杀死
for _stream in (sys.stdout, sys.stderr):
    if _stream is not None:
        try:
            _stream.reconfigure(encoding="utf-8", line_buffering=True)
        except (AttributeError, ValueError, OSError):
            pass


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def serve_running() -> bool:
    """检查是否有 app.cli serve 进程。

    wmic 在 Windows 11 24H2 已移除，统一走 scripts.procutil（CIM → wmic → tasklist）；
    探测不可用时返回 True（宽松判断，避免误杀误拉，端口检查兜底）。
    """
    try:
        from . import procutil
    except ImportError:  # 以脚本方式直接运行
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "procutil", str(Path(__file__).with_name("procutil.py"))
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        procutil = mod

    found = procutil.has_process(procutil.python_cmdlines(), "app.cli serve")
    return True if found is None else found


def port_listening() -> bool:
    try:
        import socket

        with socket.create_connection(("127.0.0.1", 8000), timeout=3):
            return True
    except OSError:
        return False


def start_serve() -> None:
    """用 breakaway 方式启动 serve（脱离宿主 Job Object）。

    不能走 powershell/serve-ctl：那条链路的子进程仍会被宿主 Job 连带杀，
    这里直接用 launch_detached.create_detached。
    """
    try:
        from .launch_detached import create_detached
    except ImportError:  # 直接以脚本方式运行
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "launch_detached", str(Path(__file__).with_name("launch_detached.py"))
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        create_detached = mod.create_detached

    py = ROOT / ".venv" / "Scripts" / "python.exe"
    try:
        pid = create_detached(
            str(py),
            ["-m", "app.cli", "serve", "--host", "0.0.0.0", "--port", "8000"],
            str(ROOT),
        )
        log(f"serve launched pid={pid}")
    except OSError as exc:
        log(f"breakaway start failed: {exc}")


def main() -> None:
    log("watchdog started")
    while True:
        try:
            proc = serve_running()
            listen = port_listening()
            if not proc or not listen:
                log(f"serve down (proc={proc} listening={listen}) -> restarting")
                start_serve()
                time.sleep(8)
                log(f"after restart: proc={serve_running()} listening={port_listening()}")
        except Exception as exc:  # noqa: BLE001 - watchdog 不能自己挂
            log(f"watchdog error: {exc}")
        time.sleep(INTERVAL)


def acquire_single_instance_lock():
    """用文件锁保证单实例。

    为什么不用"数进程个数"：venv 的 python.exe 是 trampoline 启动器，
    一个逻辑实例会派生两个命令行相同的进程（父+子），按个数判断会
    永远误认为"已有实例在跑"而直接退出，导致守护从不工作。
    Windows 在进程退出时会自动释放文件锁，所以不需要清理陈旧锁。
    """
    import msvcrt

    lock_path = ROOT / "data" / "serve_watchdog.lock"
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "a+")
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        return fh  # 必须保持打开，锁才有效
    except OSError:
        try:
            fh.close()
        except Exception:  # noqa: BLE001
            pass
        return None


if __name__ == "__main__":
    lock = acquire_single_instance_lock()
    if lock is None:
        log("serve watchdog already running - exiting")
        raise SystemExit(0)
    try:
        main()
    finally:
        lock.close()
