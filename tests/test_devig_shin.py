"""Shin 去水（P1b）：与乘法去水的数学关系 + 边界行为。"""
import pytest

from app.pricing.devig import fair_pair, fair_two_way, shin_two_way


def test_no_vig_reduces_to_multiplicative():
    # 隐含概率和恰为 1（2.0/2.0）时 shin 与乘法一致
    assert shin_two_way(2.0, 2.0) == pytest.approx((0.5, 0.5))
    m = fair_two_way(2.0, 2.0)
    assert shin_two_way(2.0, 2.0) == pytest.approx(m)


def test_sums_to_one_and_monotone_toward_favorite():
    # 大热门 1.5 / 2.75：shin 应比乘法更偏热门（冷门概率更低）
    m_fav, m_dog = fair_two_way(1.5, 2.75)
    s_fav, s_dog = shin_two_way(1.5, 2.75)
    assert s_fav + s_dog == pytest.approx(1.0)
    assert s_fav > m_fav
    assert s_dog < m_dog


def test_extreme_favorite_bias_shrinks():
    # 1.20 / 5.50：乘法给冷门 ~0.201，shin 应显著更低
    _, m_dog = fair_two_way(1.20, 5.50)
    _, s_dog = shin_two_way(1.20, 5.50)
    assert s_dog < m_dog
    assert s_dog > 0.0


def test_small_vig_close_to_multiplicative():
    # 1.90/1.90 附近，两种方法差异应 < 1pp
    m = fair_two_way(1.95, 1.95)
    s = shin_two_way(1.95, 1.95)
    assert abs(m[0] - s[0]) < 0.01


def test_invalid_inputs_return_none():
    assert shin_two_way(None, 2.0) is None
    assert shin_two_way(2.0, None) is None
    assert shin_two_way(1.0, 2.0) is None
    assert shin_two_way(2.0, 0.5) is None


def test_fair_pair_dispatch():
    assert fair_pair(2.0, 2.0, method="shin") == pytest.approx((0.5, 0.5))
    assert fair_pair(2.0, 2.0, method="multiplicative") == pytest.approx((0.5, 0.5))
    assert fair_pair(2.0, 2.0, method="unknown") == pytest.approx((0.5, 0.5))  # 未知方法回退乘法
