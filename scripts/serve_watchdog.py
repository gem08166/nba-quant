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

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)


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

    用 tasklist + 窗口标题不可靠（拿不到命令行），这里读 wmic 输出；
    wmic 不可用时退回到"端口在监听就算在跑"的宽松判断（避免误杀误拉）。
    """
    try:
        out = subprocess.run(
            [
                "wmic",
                "process",
                "where",
                "name='python.exe' or name='pythonw.exe'",
                "get",
                "CommandLine",
                "/value",
            ],
            capture_output=True,
            text=True,
            timeout=25,
            encoding="utf-8",
            errors="replace",
        ).stdout or ""
    except (OSError, subprocess.SubprocessError):
        return True  # 探测失败时不要乱杀乱拉
    if not out.strip():
        return True
    normalized = out.replace("app.cli  serve", "app.cli serve")
    return "app.cli serve" in normalized


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


if __name__ == "__main__":
    main()
