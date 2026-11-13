"""开机/登录自启：确保看门狗（serve + watch）在跑。

为什么要单独一个脚本：serve 和 watch 是"常驻进程"，机器重启后不会自己回来；
两台看门狗（scripts.serve_watchdog / scripts.watch_guard）本身也需要被拉起。
本脚本幂等，重复执行安全——两个看门狗都有文件锁，已在跑时会自己退出。

调用链：
    Windows 计划任务 NBA-AutoStart（登录时触发）
      -> pythonw -m scripts.boot_start
          -> scripts.serve_watchdog  （serve 掉了就拉）
          -> scripts.watch_guard     （watch 掉了就拉）

用法：
    python -m scripts.boot_start          # 拉起看门狗（幂等）
    python -m scripts.boot_start --check  # 只打印状态，不启动
"""
from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import procutil  # noqa: E402

LOG = ROOT / "data" / "boot_start.log"
PY = ROOT / ".venv" / "Scripts" / "python.exe"
PYW = ROOT / ".venv" / "Scripts" / "pythonw.exe"

TARGETS = [
    ("scripts.serve_watchdog", "serve watchdog"),
    ("scripts.watch_guard", "watch guard"),
]


def log(msg: str) -> None:
    line = f"[boot {datetime.now():%Y-%m-%d %H:%M:%S}] {msg}"
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass
    try:
        print(line, flush=True)
    except OSError:
        pass


def _running(needle: str) -> bool | None:
    return procutil.has_process(procutil.python_cmdlines(), needle)


def _launch(module: str) -> int:
    from scripts.launch_detached import create_detached

    exe = str(PYW if PYW.exists() else PY)
    return create_detached(exe, ["-m", module], str(ROOT))


def main() -> None:
    check_only = "--check" in sys.argv
    log("boot_start begin" + (" (check only)" if check_only else ""))

    for module, label in TARGETS:
        running = _running(module)
        if running is True:
            log(f"{label}: already running")
            continue
        if running is None:
            log(f"{label}: process probe unavailable, skip (avoid duplicates)")
            continue
        if check_only:
            log(f"{label}: NOT running (would start)")
            continue
        try:
            pid = _launch(module)
            log(f"{label}: launched pid={pid}")
        except OSError as exc:
            log(f"{label}: launch failed: {exc}")

    # 给看门狗几秒把 serve/watch 拉起来，再汇报一次（仅日志，不阻塞）
    if not check_only:
        time.sleep(6)
        for needle, label in (
            ("app.cli serve", "serve"),
            ("app.cli watch", "watch"),
        ):
            state = _running(needle)
            log(f"{label}: {'running' if state else ('unknown' if state is None else 'NOT running')}")

    log("boot_start done")


if __name__ == "__main__":
    main()
