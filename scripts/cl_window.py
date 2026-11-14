"""比赛日捕捉窗口判定（CL 会话 / 存活监控 / 开机自启共用）。

窗口定义与 scripts/cl_session.py 保持一致：
    首场开赛前 2 小时  →  末场开赛 + 3h30m

末场之后的时间要按“比赛真的打完”来算，不能按“开赛”算：2026-11-14 末场
11:30（北京）实际 13:50 才结束，旧口径 13:00 就把会话强杀，最后 3 场没收尾。

状态：
    no_games     当天（美东 slate 日）没有比赛
    all_final    全部 STATUS_FINAL —— 会话应已收工
    no_tipoffs   有比赛但时间解析不出来
    before       还没到窗口
    active       窗口内（此时必须有一个 cl_session 在跑）
    after        窗口已过
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app.config import DATA_DIR
from app.dates import current_slate_date

BEIJING = timezone(timedelta(hours=8))
EXCLUDED = ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED")

# 末场开赛后还要等多久（常规赛含暂停/加时约 2h15–2h45）
WINDOW_AFTER_LAST_TIP = timedelta(hours=3)
SAFETY_AFTER_SOFT = timedelta(minutes=30)


def window_bounds(
    first_tip: datetime, last_tip: datetime
) -> tuple[datetime, datetime, datetime]:
    """返回 (窗口开启, 软线, 硬线)。"""
    start = first_tip - timedelta(hours=2)
    soft = last_tip + WINDOW_AFTER_LAST_TIP
    hard = soft + SAFETY_AFTER_SOFT
    return start, soft, hard


def _bj(dt: datetime) -> str:
    return dt.astimezone(BEIJING).strftime("%m-%d %H:%M")


def parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def window_state(
    now: datetime | None = None,
    slate: str | None = None,
    db_path: Path | None = None,
) -> tuple[str, str]:
    """返回 (状态, 人类可读说明)。数据库打不开时返回 no_games 风格的安全值。"""
    now = now or datetime.now(timezone.utc)
    slate = slate or current_slate_date()
    path = db_path or (DATA_DIR / "nba.db")

    try:
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute(
                "SELECT tipoff, status FROM games WHERE slate_date = ? "
                f"AND status NOT IN ({','.join('?' * len(EXCLUDED))})",
                (slate, *EXCLUDED),
            ).fetchall()
        finally:
            conn.close()
    except sqlite3.Error:
        return "no_games", f"db unavailable ({path.name})"

    if not rows:
        return "no_games", f"no games on {slate}"
    if all(r["status"] == "STATUS_FINAL" for r in rows):
        return "all_final", "all games final (session done)"

    tips = [t for t in (parse_ts(r["tipoff"]) for r in rows) if t]
    if not tips:
        return "no_tipoffs", "no tipoffs parsed"

    start, _soft, end = window_bounds(min(tips), max(tips))
    if now < start:
        return "before", f"before window (starts {_bj(start)})"
    if now > end:
        return "after", f"after window (ended {_bj(end)})"
    return "active", f"window {_bj(start)}~{_bj(end)}"
