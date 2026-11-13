"""概率校准层（A 路）：Platt scaling + 向 0.5 收缩。

CLV 检验结论：模型 edge 平均 +6.07%，但按收盘价实算 ROI −10.94%，
且最高四分位（最自信）ROI −30.7%——典型的过度自信。校准层学习一个
单调映射 p → sigmoid(a·logit(p) + b)，用实际胜负拟合；a < 1 表示
向 0.5 收缩（消除过度自信），a > 1 表示模型偏保守。

按市场分别拟合（spread/total/moneyline 的偏差不一样）；样本不足或
拟合退化时回退恒等映射（a=1, b=0）。
"""
from __future__ import annotations

import math

MIN_SAMPLES = 80  # 每个市场至少这么多结算样本才拟合
# 向恒等映射收缩的先验强度：a_eff = 1 + w(a_fit − 1)，w = n/(n+PRIOR)。
# 样本 90 时 w≈0.23，拟合结果只被部分采纳；样本上千后 w→1 完全采纳。
# 这是防止小样本把参数拉到极端（实测 n=94 时 total 拟合出 a=0，
# 意味着"模型对大小分完全没有区分度"，直接砍掉整个市场太激进）。
PRIOR_STRENGTH = 300

IDENTITY = {"a": 1.0, "b": 0.0, "n": 0}


def _logit(p: float) -> float:
    p = min(max(float(p), 1e-6), 1 - 1e-6)
    return math.log(p / (1 - p))


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = math.exp(-x)
        return 1.0 / (1.0 + z)
    z = math.exp(x)
    return z / (1.0 + z)


def fit_platt(pairs: list[tuple[float, int]]) -> dict | None:
    """最小化 log loss 拟合 (p, won) 对；返回 {a, b, n}，样本不足返回 None。"""
    data = [(_logit(p), 1.0 if y else 0.0) for p, y in pairs if 0.0 < float(p) < 1.0]
    n = len(data)
    if n < MIN_SAMPLES:
        return None
    if len({y for _, y in data}) < 2:
        return None  # 全胜/全负，无法校准

    a, b = 1.0, 0.0
    lr = 0.15
    for step in range(3000):
        ga = gb = 0.0
        for x, y in data:
            q = _sigmoid(a * x + b)
            diff = q - y
            ga += diff * x
            gb += diff
        ga /= n
        gb /= n
        a -= lr * ga
        b -= lr * gb
        # 约束：a ∈ [0, 2]（不允许反转方向/爆炸），b ∈ [-3, 3]
        a = min(max(a, 0.0), 2.0)
        b = min(max(b, -3.0), 3.0)
        if abs(ga) < 1e-6 and abs(gb) < 1e-6:
            break
    # 向恒等映射收缩：小样本不要全盘采纳极端参数
    w = n / (n + PRIOR_STRENGTH)
    a_eff = 1.0 + w * (a - 1.0)
    b_eff = w * b
    return {
        "a": round(a_eff, 4),
        "b": round(b_eff, 4),
        "n": n,
        "raw_a": round(a, 4),
        "raw_b": round(b, 4),
        "w": round(w, 4),
    }


def apply(cal: dict | None, p: float) -> float:
    """按校准参数映射概率；None/恒等则原样返回。"""
    if not cal:
        return p
    a = float(cal.get("a", 1.0))
    b = float(cal.get("b", 0.0))
    if abs(a - 1.0) < 1e-9 and abs(b) < 1e-9:
        return p
    return _sigmoid(a * _logit(p) + b)
