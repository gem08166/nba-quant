# -*- coding: utf-8 -*-
"""fetch333_odds 纯函数部分：卡片解析、队名映射（不启动浏览器）。"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from scripts.fetch333_odds import ZH2EN, _parse_line, _parse_price, card_to_rows

# 2026-11-17 真实抓取的卡片（UTAH@LAC，快船主让 -6.5）
SAMPLE_CARD = {
    "home": "洛杉矶快船",
    "away": "犹他爵士",
    "odds": [
        {"tid": "oddsBtn-2|300308895|ah|h|0", "label": "-6.5", "price": "1.94"},
        {"tid": "oddsBtn-2|300308895|ah|a|0", "label": "+6.5", "price": "1.94"},
        {"tid": "oddsBtn-2|300308895|ou|ov|0", "label": "大 225.5", "price": "1.97"},
        {"tid": "oddsBtn-2|300308895|ou|ud|0", "label": "小 225.5", "price": "1.89"},
        {"tid": "oddsBtn-2|300308895|ml|h|0", "label": "主", "price": "1.39"},
        {"tid": "oddsBtn-2|300308895|ml|a|0", "label": "客", "price": "2.85"},
    ],
}


def test_parse_helpers():
    assert _parse_price("1.94") == 1.94
    assert _parse_price("") is None
    assert _parse_price("0.5") is None  # 非赔率数字
    assert _parse_line("-6.5") == -6.5
    assert _parse_line("+2.5") == 2.5
    assert _parse_line("大 225.5") is None


def test_card_to_rows_full():
    rows = card_to_rows(SAMPLE_CARD)
    by_key = {(r["market"], r["side"]): r for r in rows}
    assert len(rows) == 6
    assert by_key[("spread", "home")]["line"] == -6.5
    assert by_key[("spread", "home")]["price"] == 1.94
    assert by_key[("spread", "away")]["line"] == 6.5
    assert by_key[("total", "over")]["line"] == 225.5
    assert by_key[("total", "over")]["price"] == 1.97
    assert by_key[("total", "under")]["price"] == 1.89
    assert by_key[("moneyline", "home")]["price"] == 1.39
    assert by_key[("moneyline", "away")]["price"] == 2.85
    for r in rows:
        assert r["book"] == "333"
        assert r["source"] == "333-dom"


def test_card_ml_crosscheck_swaps_reversed_favorite():
    """主队让分但 ML 报反了（主队价更贵）→ 应交换（hao 同款校验）。"""
    card = {
        "home": "洛杉矶快船",
        "away": "犹他爵士",
        "odds": [
            {"tid": "oddsBtn-2|1|ah|h|0", "label": "-6.5", "price": "1.94"},
            {"tid": "oddsBtn-2|1|ah|a|0", "label": "+6.5", "price": "1.94"},
            {"tid": "oddsBtn-2|1|ml|h|0", "label": "主", "price": "2.85"},
            {"tid": "oddsBtn-2|1|ml|a|0", "label": "客", "price": "1.39"},
        ],
    }
    rows = card_to_rows(card)
    ml = {r["side"]: r["price"] for r in rows if r["market"] == "moneyline"}
    assert ml["home"] == 1.39
    assert ml["away"] == 2.85


def test_card_missing_name_yields_no_rows():
    assert card_to_rows({"home": None, "away": "犹他爵士", "odds": []}) == []


def test_zh2en_variants_and_coverage():
    # 333 的实际用词变体不能漏
    assert ZH2EN["克里夫兰骑士"] == "cavaliers"
    assert ZH2EN["俄克拉荷马雷霆"] == "thunder"
    # 30 支球队全在表里
    assert len({ZH2EN[k] for k in ZH2EN}) >= 30
