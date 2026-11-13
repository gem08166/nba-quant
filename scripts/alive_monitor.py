"""本地存活监控：定时检测 serve / watch / CL 任务是否存活，异常时推 ntfy。

与 watch_guard（自动重启）互补——guard 负责"拉活"，本脚本负责"告诉你挂了"。

检测项：
- serve：HTTP /api/health 是否 200
- watch：进程是否存在（复用 watch_guard 的检测）
- CL 会话：比赛日窗口期内进程是否存在（无比赛/窗口外则跳过）

任何一项异常 → ntfy 推送（带节流：同一项 30 分钟内不重复推）。
全部正常 → 可选每日心跳推送（默认关）。

用法（计划任务或手动）：
    python -m scripts.alive_monitor            # 单次检测（适合计划任务每 5 分钟跑）
    python -m scripts.alive_monitor --loop     # 常驻循环（内置 5 分钟间隔）
    python -m scripts.alive_monitor --heartbeat  # 开启每日心跳推送
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import DATA_DIR, NTFY_SERVER, NTFY_TOPIC
from app.dates import current_slate_date
from scripts import procutil

BEIJING = timezone(timedelta(hours=8))
STATE_PATH = DATA_DIR / "alive_monitor_state.json"
THROTTLE_MIN = 30  # 同一项异常的重复推送节流（分钟）
LOG_PATH = DATA_DIR / "alive_monitor.log"


def log(msg: str) -> None:
    ts = datetime.now(BEIJING).strftime("%m-%d %H:%M:%S")
    line = f"[alive {ts}] {msg}"
    print(line, flush=True)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except OSError:
        pass


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    except OSError:
        pass


def _ntfy_send(title: str, text: str) -> bool:
    if not NTFY_TOPIC:
        return False
    try:
        resp = httpx.post(
            f"{NTFY_SERVER}/{NTFY_TOPIC}",
            content=text.encode("utf-8"),
            headers={"Title": title.encode("utf-8"), "Tags": "warning", "Priority": "high"},
            timeout=15,
        )
        return resp.status_code in (200, 204)
    except Exception:
        return False


def check_serve() -> tuple[bool, str]:
    """serve 存活：HTTP /api/health 200 且 ok=true。"""
    try:
        resp = httpx.get("http://127.0.0.1:8000/api/health", timeout=10)
        if resp.status_code == 200 and resp.json().get("ok"):
            return True, "ok"
        return False, f"HTTP {resp.status_code}"
    except Exception as exc:
        return False, str(exc)[:80]


def _python_cmdlines() -> str | None:
    """列出所有 python 进程命令行；查询失败返回 None（调用方保守视为存活）。"""
    return procutil.python_cmdlines()


def check_watch() -> tuple[bool, str]:
    """watch 存活：进程列表含 app.cli watch。"""
    out = _python_cmdlines()
    if out is None:
        return True, "process list unavailable (assume ok)"
    if not out.strip():
        return False, "no python process found"
    return ("app.cli watch" in out), ("watch process found" if "app.cli watch" in out else "watch not in process list")


def check_cl() -> tuple[bool, str]:
    """CL 会话存活：比赛日窗口期（首场前 2h → 末场后 1h）内进程必须存在。"""
    out = _python_cmdlines()
    if out is None:
        return True, "process list unavailable (assume ok)"
    cl_running = "cl_session" in out

    conn = sqlite3.connect(str(DATA_DIR / "nba.db"))
    conn.row_factory = sqlite3.Row
    try:
        slate = current_slate_date()
        games = conn.execute(
            "SELECT tipoff, status FROM games WHERE slate_date = ? "
            "AND status NOT IN ('STATUS_POSTPONED','STATUS_CANCELED','STATUS_CANCELLED')",
            (slate,),
        ).fetchall()
        if not games:
            return True, "no games today"

        # 全部完赛 = 会话正常收工，窗口尾部不再要求进程存在（避免误报）
        if all(g["status"] == "STATUS_FINAL" for g in games):
            return True, "all games final (session done)"

        now = datetime.now(timezone.utc)
        tips = []
        for g in games:
            try:
                tip = datetime.fromisoformat(str(g["tipoff"]).replace("Z", "+00:00"))
                if tip.tzinfo is None:
                    tip = tip.replace(tzinfo=timezone.utc)
                tips.append(tip)
            except ValueError:
                continue
        if not tips:
            return True, "no tipoffs parsed"

        first, last = min(tips), max(tips)
        window_start = first - timedelta(hours=2)
        window_end = last + timedelta(hours=1)

        if not (window_start <= now <= window_end):
            return True, f"outside window ({_bj(now)} not in {_bj(window_start)}~{_bj(window_end)})"

        if cl_running:
            return True, "running in window"
        return False, f"window active but cl_session not running"
    finally:
        conn.close()


def _bj(dt: datetime) -> str:
    return dt.astimezone(BEIJING).strftime("%m-%d %H:%M")


def check_guards() -> tuple[bool, str]:
    """看守狗（serve_watchdog / watch_guard）是否在跑；进程探测不可用时视为 ok。"""
    out = _python_cmdlines()
    if out is None:
        return True, "process list unavailable (assume ok)"
    normalized = " ".join(out.split())
    missing = [
        name
        for name, needle in (
            ("serve_watchdog", "scripts.serve_watchdog"),
            ("watch_guard", "scripts.watch_guard"),
        )
        if needle not in normalized
    ]
    if not missing:
        return True, "both guards running"
    return False, "missing: " + ", ".join(missing)


def run_checks() -> list[tuple[str, bool, str]]:
    results = []
    ok, msg = check_serve()
    results.append(("serve", ok, msg))
    ok, msg = check_watch()
    results.append(("watch", ok, msg))
    ok, msg = check_cl()
    results.append(("cl_session", ok, msg))
    ok, msg = check_guards()
    results.append(("guards", ok, msg))
    return results


def notify_down(down: list[tuple[str, str]]) -> bool:
    lines = []
    for name, reason in down:
        lines.append(f"× {name}: {reason}")
    text = "\n".join(lines) + f"\n\n检测时间：{datetime.now(BEIJING).strftime('%m-%d %H:%M')}\n修复：python -m scripts.alive_monitor 或 watch-ctl/serve-ctl restart"
    return _ntfy_send("NBA Quant 服务异常", text)


def run_once(heartbeat: bool = False) -> None:
    state = _load_state()
    results = run_checks()
    down = [(name, msg) for name, ok, msg in results if not ok]
    down_names = {name for name, _ in down}

    now_iso = datetime.now(timezone.utc).isoformat(timespec="seconds")
    alerted = state.setdefault("alerted", {})

    if down:
        # 节流：同一项 30 分钟内不重复推
        should_push = []
        for name, reason in down:
            last_alert = alerted.get(name, "")
            try:
                last_ts = datetime.fromisoformat(last_alert) if last_alert else None
            except ValueError:
                last_ts = None
            if not last_ts or (datetime.now(timezone.utc) - last_ts).total_seconds() > THROTTLE_MIN * 60:
                should_push.append((name, reason))

        if should_push:
            if notify_down(should_push):
                for name, _ in should_push:
                    alerted[name] = now_iso
                log(f"ALERT pushed: {', '.join(n for n, _ in should_push)}")
            else:
                log(f"ALERT push FAILED: {', '.join(n for n, _ in should_push)}")
    else:
        # 全部正常：清空告警状态，并在恢复时推一条
        if alerted:
            recovered = ", ".join(sorted(alerted))
            if _ntfy_send(
                "NBA Quant 已恢复",
                f"× {recovered} 已恢复正常\n\n{datetime.now(BEIJING).strftime('%m-%d %H:%M')}",
            ):
                log(f"recovered, notified: {recovered}")
            else:
                log(f"recovered (push failed): {recovered}")
            state["alerted"] = {}

        if heartbeat:
            # 每日心跳：只推一次（state 里记日期）
            today = datetime.now(BEIJING).strftime("%Y-%m-%d")
            if state.get("last_heartbeat") != today:
                if _ntfy_send("NBA Quant 心跳", f"全部存活 · {len(results)} 项检查通过\n{datetime.now(BEIJING).strftime('%m-%d %H:%M')}"):
                    state["last_heartbeat"] = today
                    log("heartbeat pushed")
                else:
                    log("heartbeat push failed")

    for name, ok, msg in results:
        log(f"{name}: {'ok' if ok else 'DOWN'} ({msg})")
    _save_state(state)


def main() -> None:
    parser = argparse.ArgumentParser(description="本地存活监控推送")
    parser.add_argument("--loop", action="store_true", help="常驻循环（5 分钟一轮）")
    parser.add_argument("--interval", type=int, default=300, help="循环间隔（秒）")
    parser.add_argument("--heartbeat", action="store_true", help="每日心跳推送")
    args = parser.parse_args()

    log(f"alive monitor started (loop={args.loop}, interval={args.interval}s)")
    if args.loop:
        while True:
            run_once(heartbeat=args.heartbeat)
            time.sleep(args.interval)
    else:
        run_once(heartbeat=args.heartbeat)


if __name__ == "__main__":
    main()
