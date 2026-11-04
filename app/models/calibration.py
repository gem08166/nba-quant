from __future__ import annotations

"""Isotonic 回归（PAVA 池邻违例算法）——模型概率的校准层。

用途：walk-forward 回测产出的 out-of-sample 原始概率 (x) 对实际结果 (y)
拟合单调映射，消除模型系统性偏移（实测：均势局高估 3.5pp、强队局低估 5pp）。
使用顺序（第三方审查要求）：先校准原始概率，再与市场融合；不校准融合后概率。
"""

from typing import Any


def isotonic_fit(xs: list[float], ys: list[float]) -> list[list[float]]:
    """PAVA：返回块列表 [weight, mean, x_start, x_end]（按 x 升序，均值单调递增）。"""
    if len(xs) != len(ys) or not xs:
        return []
    blocks: list[list[float]] = []
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    for i in order:
        x, y = float(xs[i]), float(ys[i])
        blocks.append([1.0, y, x, x])
        while len(blocks) >= 2 and blocks[-2][1] > blocks[-1][1]:
            w1, m1, s1, e1 = blocks[-2]
            w2, m2, s2, e2 = blocks[-1]
            w = w1 + w2
            m = (w1 * m1 + w2 * m2) / w
            blocks[-2:] = [[w, m, s1, e2]]
    return blocks


def isotonic_predict(blocks: list[list[float]], x: float) -> float:
    """预测：落在块内取块均值；块间线性插值；越界取边界块均值。"""
    if not blocks:
        return x
    if x <= blocks[0][2]:
        return blocks[0][1]
    if x >= blocks[-1][3]:
        return blocks[-1][1]
    for i, block in enumerate(blocks):
        s, e = block[2], block[3]
        if s <= x <= e:
            return block[1]
        if i + 1 < len(blocks) and e < x < blocks[i + 1][2]:
            gap = blocks[i + 1][2] - e
            frac = (x - e) / gap if gap > 0 else 0.0
            return block[1] + frac * (blocks[i + 1][1] - block[1])
    return blocks[-1][1]


def ece(probs: list[float], outcomes: list[float], bins: int = 10) -> float:
    """期望校准误差（加权 |预测均值 − 实际频率|）。"""
    if not probs:
        return 0.0
    bucket_sum: dict[int, float] = {}
    bucket_count: dict[int, int] = {}
    for p, o in zip(probs, outcomes):
        b = min(int(p * bins), bins - 1)
        bucket_sum[b] = bucket_sum.get(b, 0.0) + o
        bucket_count[b] = bucket_count.get(b, 0) + 1
    total = len(probs)
    err = 0.0
    for b in bucket_count:
        err += bucket_count[b] / total * abs(bucket_sum[b] / bucket_count[b] - (b + 0.5) / bins)
    return err
