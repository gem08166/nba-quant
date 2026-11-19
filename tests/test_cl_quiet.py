# -*- coding: utf-8 -*-
"""CL 晚间静默窗口（2026-11-19 用户规则：北京 19:00~24:00 不跑 CL）。"""
from datetime import datetime, timedelta, timezone

from app.dates import cl_quiet_active
from scripts.cl_schedule import _clamp_quiet

BEIJING = timezone(timedelta(hours=8))


def _bj(hhmm: str) -> datetime:
    h, m = map(int, hhmm.split(":"))
    return datetime(2026, 11, 19, h, m, tzinfo=BEIJING)


def test_quiet_window_active():
    for t in ("19:00", "19:30", "21:00", "23:59"):
        assert cl_quiet_active(_bj(t)), t


def test_outside_quiet_window():
    for t in ("00:00", "06:00", "12:00", "18:59"):
        assert not cl_quiet_active(_bj(t)), t


def test_clamp_moves_evening_target_to_midnight():
    assert _clamp_quiet("19:00") == "00:00"
    assert _clamp_quiet("21:30") == "00:00"
    assert _clamp_quiet("23:59") == "00:00"


def test_clamp_keeps_daytime_target():
    for t in ("06:00", "08:30", "12:00", "18:59"):
        assert _clamp_quiet(t) == t


def test_boundary_is_half_open():
    # 24:00 == 00:00 次日：不在窗口内
    assert not cl_quiet_active(_bj("00:00"))
