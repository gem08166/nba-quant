"""赛中胜率模型（研究模块）：基本性质与边界。"""
import math

from app.models import live_wp


def test_tipoff_is_fifty_fifty():
    """开赛瞬间分差 0、无赛前预期 → 50%。"""
    p = live_wp.win_prob(0, live_wp.T_REGULATION, 0.0)
    assert abs(p - 0.5) < 1e-9


def test_win_prob_monotonic_in_margin():
    """剩余时间相同时，领先越多胜率越高。"""
    t = 600.0
    probs = [live_wp.win_prob(m, t) for m in (-20, -10, -5, 0, 5, 10, 20)]
    assert probs == sorted(probs)
    assert probs[0] < 0.05 and probs[-1] > 0.95


def test_win_prob_monotonic_in_time():
    """同一分差下，越接近终场越确定。"""
    t_values = [2400, 1800, 900, 300, 60]
    probs = [live_wp.win_prob(6, t) for t in t_values]
    assert probs == sorted(probs)
    assert probs[-1] > 0.8


def test_prior_pulls_toward_pregame_expectation():
    """无分差时，赛前预期为正会抬高主胜概率，但幅度随剩余时间衰减。"""
    early = live_wp.win_prob(0, 2400, prior_margin=8.0)
    late = live_wp.win_prob(0, 60, prior_margin=8.0)
    assert early > 0.5
    assert abs(late - 0.5) < abs(early - 0.5)


def test_overtime_uses_period_clock():
    """加时按当前加时剩余时钟算，而不是常规时间归零。"""
    assert live_wp.seconds_remaining(4, 0) == 0.0
    assert live_wp.seconds_remaining(5, 240) == 240.0
    assert math.isclose(live_wp.seconds_remaining(1, 720), live_wp.T_REGULATION)


def test_seconds_remaining_quarter_progress():
    # 第 2 节还剩 6:00 → 已打 720 + 360 = 1080，剩 1800
    assert math.isclose(live_wp.seconds_remaining(2, 360), 1800.0)


def test_params_roundtrip(tmp_path, monkeypatch):
    """参数文件可读写；缺失时回落到默认值。"""
    monkeypatch.setattr(live_wp, "PARAMS_PATH", tmp_path / "p.json")
    assert live_wp.load_params() == live_wp.DEFAULT_PARAMS

    (tmp_path / "p.json").write_text(
        '{"prior_weight": 1.0, "sigma_full": 24.0, "gamma": 0.6, "sigma_floor": 1.6}',
        encoding="utf-8",
    )
    params = live_wp.load_params()
    assert params["sigma_full"] == 24.0
    assert params["gamma"] == 0.6
