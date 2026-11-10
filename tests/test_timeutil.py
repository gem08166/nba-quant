"""时间口径：真实 ET（含夏令时）/ 北京日期换算。"""
from datetime import datetime, timezone

from app import timeutil
from app.dates import current_slate_date


def test_eastern_date_handles_dst():
    # 夏令时（EDT, UTC−4）：04:30Z 仍是 7 月 1 日 00:30 ET
    assert timeutil.eastern_date("2026-07-01T04:30:00Z") == "2026-07-01"
    assert timeutil.eastern_date("2026-07-01T03:30:00Z") == "2026-06-30"
    # 冬令时（EST, UTC−5）：04:30Z 是 11 月 30 日 23:30 ET
    assert timeutil.eastern_date("2026-12-01T04:30:00Z") == "2026-11-30"


def test_current_slate_date_uses_eastern():
    assert current_slate_date() == timeutil.eastern_date()


def test_beijing_slate_date_is_next_day():
    assert timeutil.beijing_slate_date("2026-11-09") == "2026-11-10"
    assert timeutil.beijing_slate_date("") == ""


def test_parse_iso_assumes_utc_and_formats_beijing():
    assert timeutil.parse_iso("2026-11-10T00:00:00").tzinfo == timezone.utc
    assert timeutil.fmt_beijing("2026-11-10T00:00:00Z") == "11-10 08:00"
    assert timeutil.fmt_beijing(None) == "时间待定"


def test_today_beijing_is_a_date_string():
    value = timeutil.today_beijing()
    assert len(value) == 10 and value[4] == "-" and value[7] == "-"
    datetime.fromisoformat(value)  # 不抛异常即可


def test_to_beijing_with_datetime():
    dt = datetime(2026, 11, 9, 16, 30, tzinfo=timezone.utc)  # 北京 11-10 00:30
    assert timeutil.to_beijing(dt).strftime("%Y-%m-%d %H:%M") == "2026-11-10 00:30"
