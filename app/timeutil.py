"""统一时间口径。

三层语义，别再混：

1. **存储**：所有时间戳都是带时区的 ISO（`placed_at`、`captured_at`、`created_at` …）；
   数据库里的 `slate_date` 是 **NBA 比赛日（美国东部日期）**，不是北京日期。
2. **计算**：用真实的 `America/New_York`（含夏令时）判断比赛日；
   `Asia/Shanghai` 固定 UTC+8（中国无夏令时）。
3. **展示**：所有具体时刻按北京时间；收益页/复盘页的「比赛日」显示为
   `slate_date + 1` 天（北京观众看球在次日凌晨/上午）。

Windows 上 `zoneinfo` 需要 `tzdata`（pyproject 已声明依赖）；缺失时退回固定
UTC−5 / UTC+8，保证程序仍能跑（DST 期间会有 1 小时偏差）。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

try:
    BEIJING = ZoneInfo("Asia/Shanghai")
    EASTERN = ZoneInfo("America/New_York")
    TZ_AVAILABLE = True
except ZoneInfoNotFoundError:  # Windows 未装 tzdata
    BEIJING = timezone(timedelta(hours=8))
    EASTERN = timezone(timedelta(hours=-5))
    TZ_AVAILABLE = False


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_iso() -> str:
    return now_utc().isoformat(timespec="seconds")


def parse_iso(value) -> datetime | None:
    """解析 ISO 时间；无时区按 UTC 处理。失败返回 None。"""
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def to_beijing(value) -> datetime | None:
    dt = parse_iso(value) if not isinstance(value, datetime) else value
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BEIJING)


def fmt_beijing(value, fmt: str = "%m-%d %H:%M") -> str:
    dt = to_beijing(value)
    return dt.strftime(fmt) if dt else "时间待定"


def eastern_date(value=None) -> str:
    """某个时刻的 NBA 比赛日（美国东部日期）。"""
    dt = parse_iso(value) if not isinstance(value, datetime) else value
    if dt is None:
        dt = now_utc()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(EASTERN).date().isoformat()


def today_beijing() -> str:
    return now_utc().astimezone(BEIJING).date().isoformat()


def beijing_slate_date(slate: str) -> str:
    """ET 比赛日 → 北京日期（NBA 晚场比赛对应北京次日）。"""
    if not slate:
        return ""
    try:
        return (date.fromisoformat(slate[:10]) + timedelta(days=1)).isoformat()
    except ValueError:
        return slate
