# -*- coding: utf-8 -*-
"""CL 触发器动态调度：把 NBA-CL-Session 的每日触发时刻改为「下一场开球前 2 小时」。

由 NBA-CL-Scheduler 计划任务每 30 分钟调用一次（pythonw 静默运行）：

    pythonw -m scripts.cl_schedule

逻辑：
1. 读 data/nba.db 里下一场未终场比赛的开球时间（跨日期取最早）。
2. 目标触发时刻 = 开球前 2 小时（北京时间 HH:MM）。
   - 若窗口已经错过（目标时刻早于现在且开球就在今天）→ 改为 2 分钟后立即触发
     （cl_session 对迟到触发的处理是跳过等待直接进入捕捉）。
   - 若库里没有任何未终场的未来比赛 → 不动触发器（保留现状）。
3. 与任务当前触发时刻不同才改（schtasks /change /st），避免无谓写任务。
4. 决策追加写入 data/cl_schedule.log（一行一条，便于排查）。

注：cl_session 自带单实例锁 + 窗口内延迟/取消自检，触发器只负责「准点开火」。
"""
from __future__ import annotations

import re
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import CL_QUIET_END_H, CL_QUIET_START_H, DATA_DIR

BEIJING = timezone(timedelta(hours=8))
TASK_NAME = "NBA-CL-Session"
LEAD_HOURS = 2
EXCLUDED = ("STATUS_FINAL", "STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED")
LOG = Path(DATA_DIR) / "cl_schedule.log"


def _clamp_quiet(hhmm: str) -> str:
    """目标触发时刻落在 CL 静默窗口（默认北京 19:00~24:00）内 → 挪到窗口结束。

    用户规则（2026-11-19）：晚上七点至十二点前不再 CL。cl_session 侧也有
    同款守卫（含 --force 豁免），这里把计划任务触发点直接排到窗口外，
    免得任务启动后立刻空跑退出。
    """
    h, m = hhmm.split(":")
    t = int(h) + int(m) / 60.0
    start, end = CL_QUIET_START_H, CL_QUIET_END_H
    in_quiet = start <= t < end if start <= end else (t >= start or t < end)
    if not in_quiet:
        return hhmm
    end_h = int(end) % 24
    end_m = int((end % 1) * 60)
    return f"{end_h:02d}:{end_m:02d}"


def _log(msg: str) -> None:
    line = f"{datetime.now(BEIJING).strftime('%m-%d %H:%M')} {msg}"
    print(line)
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass


def _parse_ts(value: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _next_tipoff(conn: sqlite3.Connection) -> datetime | None:
    now = datetime.now(timezone.utc)
    rows = conn.execute(
        "SELECT tipoff FROM games "
        "WHERE status NOT IN (%s) AND tipoff IS NOT NULL" % ",".join("?" * len(EXCLUDED)),
        EXCLUDED,
    ).fetchall()
    tips = [t for t in (_parse_ts(r[0]) for r in rows) if t and t > now]
    return min(tips) if tips else None


def _current_trigger_time() -> str | None:
    """读任务的每日触发时刻（本地 HH:MM）。"""
    try:
        out = subprocess.run(
            ["schtasks", "/query", "/tn", TASK_NAME, "/xml"],
            capture_output=True, text=True, timeout=30, check=True,
        ).stdout
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        _log(f"query task failed: {e}")
        return None
    m = re.search(r"<StartBoundary>([\d\-T:.+]+?)</StartBoundary>", out)
    if not m:
        return None
    try:
        dt = datetime.fromisoformat(m.group(1))
    except ValueError:
        return None
    return dt.strftime("%H:%M")


def _set_trigger(hhmm: str) -> bool:
    try:
        subprocess.run(
            ["schtasks", "/change", "/tn", TASK_NAME, "/st", hhmm],
            capture_output=True, text=True, timeout=30, check=True,
        )
        return True
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        err = getattr(e, "stderr", "") or ""
        _log(f"set trigger {hhmm} failed: {str(e)[:80]} {err[:120]}")
        return False


def main() -> None:
    conn = sqlite3.connect(Path(DATA_DIR) / "nba.db")
    conn.row_factory = sqlite3.Row
    try:
        tip = _next_tipoff(conn)
    finally:
        conn.close()

    now_bj = datetime.now(BEIJING)
    if tip is None:
        _log("no upcoming games — trigger unchanged")
        return

    window_start = tip - timedelta(hours=LEAD_HOURS)
    tip_bj = tip.astimezone(BEIJING)
    win_bj = window_start.astimezone(BEIJING)

    if win_bj.date() == now_bj.date() and win_bj <= now_bj:
        # 窗口已开（或刚过）且开球就在今天：尽快触发，cl_session 会直接进入捕捉
        target = (now_bj + timedelta(minutes=2)).strftime("%H:%M")
        reason = f"window open/missed (tip {tip_bj:%m-%d %H:%M}) -> fire ASAP"
    else:
        target = win_bj.strftime("%H:%M")
        reason = f"tip {tip_bj:%m-%d %H:%M} - {LEAD_HOURS}h"

    cur = _current_trigger_time()
    if cur is None:
        _log(f"cannot read current trigger; skip (want {target})")
        return
    clamped = _clamp_quiet(target)
    if clamped != target:
        _log(f"target {target} 在静默窗口（{CL_QUIET_START_H:g}~{CL_QUIET_END_H:g}h）内 -> 改 {clamped}")
        target = clamped
    if cur == target:
        _log(f"trigger already {target} ({reason})")
        return
    if _set_trigger(target):
        _log(f"trigger {cur} -> {target} ({reason})")


if __name__ == "__main__":
    main()
