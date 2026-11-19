from __future__ import annotations

from datetime import datetime

from .config import CL_QUIET_END_H, CL_QUIET_START_H
from .timeutil import eastern_date


def current_slate_date() -> str:
    """The NBA slate currently in progress / upcoming next.

    ESPN keys slates by US Eastern date; tip-offs run from ~00:00Z to ~03:30Z
    of the following UTC day. 用真实 America/New_York（含夏令时）计算，
    避免 3–11 月 DST 期间边界日期错一天。
    """
    return eastern_date()


def cl_quiet_active(now_bj: datetime) -> bool:
    """北京时间 now 是否处于 CL 静默窗口（默认 19:00 ≤ t < 24:00）。

    用户规则（2026-11-19）：晚上七点至十二点前不再跑 CL——晚间不启动/不捕捉/
    不推送，--force 手动运行除外。窗口含头不含尾，小时数可由
    NBAQ_CL_QUIET_START / NBAQ_CL_QUIET_END 调整（支持小数）。
    """
    t = now_bj.hour + now_bj.minute / 60.0 + now_bj.second / 3600.0
    if CL_QUIET_START_H <= CL_QUIET_END_H:
        return CL_QUIET_START_H <= t < CL_QUIET_END_H
    # 跨午夜窗口（如 22 → 6）
    return t >= CL_QUIET_START_H or t < CL_QUIET_END_H
