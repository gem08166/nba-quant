from __future__ import annotations

from .timeutil import eastern_date


def current_slate_date() -> str:
    """The NBA slate currently in progress / upcoming next.

    ESPN keys slates by US Eastern date; tip-offs run from ~00:00Z to ~03:30Z
    of the following UTC day. 用真实 America/New_York（含夏令时）计算，
    避免 3–11 月 DST 期间边界日期错一天。
    """
    return eastern_date()
