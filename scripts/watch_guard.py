"""watch 常驻守护：watch 进程消失就自动拉起。

与 serve_watchdog 同理，但守护的是 `app.cli watch`（15 分钟一次的采集 +
结算 + 信号推送）。watch 崩溃会导致数据停止更新，所以必须有兜底。

用法：
    python -m scripts.watch_guard
日志：data/watch-guard.log
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOG = ROOT / "data" / "watch-guard.log"
INTERVAL = 120

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)


def log(msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _load_launcher():
    try:
        from .launch_detached import create_detached

        return create_detached
    except ImportError:
        spec = importlib.util.spec_from_file_location(
            "launch_detached", str(Path(__file__).with_name("launch_detached.py"))
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod.create_detached


def watch_running() -> bool:
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
        return True
    if not out.strip():
        return True
    return "app.cli watch" in out.replace("app.cli  watch", "app.cli watch")


def start_watch() -> None:
    create_detached = _load_launcher()
    py = ROOT / ".venv" / "Scripts" / "python.exe"
    try:
        pid = create_detached(str(py), ["-m", "app.cli", "watch"], str(ROOT))
        log(f"watch launched pid={pid}")
    except OSError as exc:
        log(f"breakaway start failed: {exc}")


def main() -> None:
    log("watch guard started")
    while True:
        try:
            if not watch_running():
                log("watch down -> restarting")
                start_watch()
                time.sleep(20)
                log(f"after restart: running={watch_running()}")
        except Exception as exc:  # noqa: BLE001
            log(f"guard error: {exc}")
        time.sleep(INTERVAL)


def acquire_single_instance_lock():
    """用文件锁保证单实例（venv trampoline 会让一个实例显示成两个进程，
    按进程个数判断会误判，详见 serve_watchdog.py 的说明）。"""
    import msvcrt

    lock_path = ROOT / "data" / "watch_guard.lock"
    try:
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(lock_path, "a+")
        msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        return fh
    except OSError:
        try:
            fh.close()
        except Exception:  # noqa: BLE001
            pass
        return None


if __name__ == "__main__":
    lock = acquire_single_instance_lock()
    if lock is None:
        log("watch guard already running - exiting")
        raise SystemExit(0)
    try:
        main()
    finally:
        lock.close()
