from __future__ import annotations

import math

SQRT2 = math.sqrt(2.0)


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / SQRT2))


def norm_pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def american_to_decimal(value: float | int | str | None) -> float | None:
    if value in (None, ""):
        return None
    try:
        american = float(str(value).strip().replace("+", "").replace("−", "-"))
    except ValueError:
        return None
    if american == 0:
        return None
    return 1.0 + american / 100.0 if american > 0 else 1.0 + 100.0 / abs(american)


def decimal_to_american(decimal: float) -> int:
    if decimal >= 2.0:
        return round((decimal - 1.0) * 100)
    return -round(100.0 / (decimal - 1.0))


def implied_probability(decimal: float) -> float:
    return 1.0 / decimal


def devig_multiplicative(prices: list[float]) -> list[float]:
    """Normalize implied probabilities so they sum to one (basic vig removal)."""
    implied = [1.0 / p for p in prices if p and p > 1.0]
    total = sum(implied)
    if total <= 0:
        return [1.0 / len(prices)] * len(prices)
    return [p / total for p in implied]


def fair_two_way(price_a: float | None, price_b: float | None) -> tuple[float, float] | None:
    """Return fair probabilities for a two-sided market.

    单边报价返回 None（无法去水）——调用方应跳过该侧 edge，
    否则 0.5/0.5 的伪公允概率会与单边价格组合出假信号。
    """
    if price_a and price_b and price_a > 1.0 and price_b > 1.0:
        a, b = devig_multiplicative([price_a, price_b])
        return a, b
    return None


def cover_prob(mu_margin: float, home_line: float, sigma: float) -> float:
    """P(home covers), where home_line is the home spread (e.g. -5.5).

    Integer lines can push: return P(win) + 0.5·P(push) using a continuity
    correction on the integer margin distribution. Half/quarter lines have no
    push, so they use the plain normal tail.
    """
    if sigma <= 0:
        raise ValueError("sigma must be positive")
    target = -home_line
    nearest = round(target)
    if abs(target - nearest) < 1e-9:
        # P(margin > k) + 0.5·P(margin = k)
        return 1.0 - 0.5 * (
            norm_cdf((nearest + 0.5 - mu_margin) / sigma)
            + norm_cdf((nearest - 0.5 - mu_margin) / sigma)
        )
    return 1.0 - norm_cdf((target - mu_margin) / sigma)


def over_prob(mu_total: float, total_line: float, sigma: float) -> float:
    """P(over) with the same push-aware integer-line treatment as cover_prob."""
    if sigma <= 0:
        raise ValueError("sigma must be positive")
    nearest = round(total_line)
    if abs(total_line - nearest) < 1e-9:
        return 1.0 - 0.5 * (
            norm_cdf((nearest + 0.5 - mu_total) / sigma)
            + norm_cdf((nearest - 0.5 - mu_total) / sigma)
        )
    return 1.0 - norm_cdf((total_line - mu_total) / sigma)


def moneyline_prob(mu_margin: float, sigma: float) -> float:
    if sigma <= 0:
        raise ValueError("sigma must be positive")
    return norm_cdf(mu_margin / sigma)


def expected_value(prob: float, decimal_odds: float) -> float:
    return prob * decimal_odds - 1.0


def kelly_fraction(prob: float, decimal_odds: float) -> float:
    if decimal_odds <= 1.0:
        return 0.0
    return max(0.0, (prob * decimal_odds - 1.0) / (decimal_odds - 1.0))


def edge(prob: float, fair_prob: float) -> float:
    return prob - fair_prob
