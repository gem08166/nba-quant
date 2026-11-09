"""推送文案时间统一北京时间。"""
from app.jobs.notify import _fmt_beijing, format_signal


def test_fmt_beijing_converts_utc():
    assert _fmt_beijing("2026-11-10T00:00:00Z") == "11-10 08:00"
    assert _fmt_beijing("2026-11-10T00:00:00+00:00") == "11-10 08:00"
    # 无时区的 ISO 按 UTC 处理
    assert _fmt_beijing("2026-11-09T16:30:00") == "11-10 00:30"


def test_fmt_beijing_handles_empty_and_bad_values():
    assert _fmt_beijing(None) == "时间待定"
    assert _fmt_beijing("") == "时间待定"
    assert _fmt_beijing("not-a-time") == "not-a-time"


def test_format_signal_shows_beijing_tipoff():
    game = {
        "home_abbrev": "LAL",
        "away_abbrev": "BOS",
        "tipoff": "2026-11-10T03:30:00Z",  # 北京时间 11:30
    }
    edge = {
        "market": "spread",
        "side": "home",
        "line": -2.5,
        "price": 1.95,
        "book": "draftkings",
        "model_prob": 0.55,
        "fair_prob": 0.52,
        "edge": 0.03,
        "ev": 0.07,
        "stake_pct": 0.25,
    }
    text = format_signal(game, edge)
    assert "北京时间" in text
    assert "11-10 11:30" in text
    assert "2026-11-10T03:30:00Z" not in text
