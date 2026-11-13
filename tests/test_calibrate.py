"""A 路：Platt 校准的拟合/收缩/应用。"""
import math

from app.pricing import calibrate as cal


def _logit(p):
    return math.log(p / (1 - p))


def _sigmoid(x):
    return 1.0 / (1.0 + math.exp(-x))


def test_fit_requires_min_samples():
    assert cal.fit_platt([(0.6, 1)] * 10) is None


def test_fit_recovers_overconfidence_and_shrinks_toward_identity():
    # 过度自信数据：模型总说 0.9，实际只赢 60%
    pairs = [(0.9, 1 if i % 10 < 6 else 0) for i in range(200)]
    fit = cal.fit_platt(pairs)
    assert fit is not None
    assert fit["raw_a"] < 1.0            # 原始拟合确实在收缩
    assert 1.0 > fit["a"] > fit["raw_a"]  # 经验贝叶斯后更靠近恒等
    assert fit["n"] == 200
    assert 0.0 < fit["w"] < 1.0


def test_apply_identity_and_shrink():
    assert cal.apply(None, 0.62) == 0.62
    assert cal.apply({"a": 1.0, "b": 0.0}, 0.62) == 0.62
    # a<1 把概率拉向 0.5；a>1 推离 0.5
    assert cal.apply({"a": 0.5, "b": 0.0}, 0.62) < 0.62
    assert cal.apply({"a": 1.5, "b": 0.0}, 0.62) > 0.62


def test_apply_matches_logit_formula():
    a, b, p = 0.8, 0.1, 0.7
    assert abs(cal.apply({"a": a, "b": b}, p) - _sigmoid(a * _logit(p) + b)) < 1e-12
