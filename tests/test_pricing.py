import math

import pytest

from app.pricing.devig import (
    american_to_decimal,
    cover_prob,
    devig_multiplicative,
    kelly_fraction,
    moneyline_prob,
    over_prob,
)


def test_american_to_decimal():
    assert american_to_decimal("-110") == pytest.approx(1.90909, abs=1e-4)
    assert american_to_decimal("+150") == pytest.approx(2.5)
    assert american_to_decimal(None) is None


def test_devig_two_way():
    probs = devig_multiplicative([1.90909, 1.90909])
    assert sum(probs) == pytest.approx(1.0)
    assert probs[0] == pytest.approx(0.5)


def test_cover_prob_favorite():
    # Home -5.5, model margin +7, sigma 8 -> home covers ~57.4%
    assert cover_prob(7, -5.5, 8) == pytest.approx(0.5744, abs=0.002)


def test_cover_prob_underdog():
    # Home +5.5, model margin +1 -> home covers ~79.2%
    assert cover_prob(1, 5.5, 8) == pytest.approx(0.7917, abs=0.002)


def test_over_prob():
    assert over_prob(225, 219.5, 14) == pytest.approx(0.6528, abs=0.002)
    assert over_prob(225, 220, 14) == pytest.approx(0.6260, abs=0.002)


def test_moneyline_prob():
    assert moneyline_prob(0, 12) == pytest.approx(0.5)
    assert moneyline_prob(6, 12) == pytest.approx(0.6915, abs=0.002)


def test_kelly():
    assert kelly_fraction(0.55, 2.0) == pytest.approx(0.10)
    assert kelly_fraction(0.50, 1.91) == 0.0
