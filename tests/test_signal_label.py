"""复盘信号标注：bettable_label 的三种取值与 reasons/schema_old 口径。"""
from collections import Counter

from app.jobs.live import _bettable_label


def test_bettable_ever_wins():
    g = {"bettable_ever": True, "gated_ever": False, "schema_old": False,
         "reasons": Counter({"共识书源数 1 < 3": 5})}
    assert _bettable_label(g) == "可下注"


def test_old_schema_without_note():
    g = {"bettable_ever": False, "gated_ever": False, "schema_old": True, "reasons": Counter()}
    assert _bettable_label(g) == "旧数据(无闸门)"


def test_soft_book_reason_shortened():
    g = {
        "bettable_ever": False, "gated_ever": False, "schema_old": False,
        "reasons": Counter({"最优价来自软书/聚合源 hao-tiyu（延迟线/限注单风险）": 33}),
    }
    assert _bettable_label(g) == "观察·软书hao-tiyu"


def test_books_count_reason_shortened():
    g = {
        "bettable_ever": False, "gated_ever": False, "schema_old": False,
        "reasons": Counter({"共识书源数 2 < 3（定价基准不可靠）": 24}),
    }
    assert _bettable_label(g) == "观察·仅2家书源"


def test_gated_fallback():
    g = {"bettable_ever": False, "gated_ever": True, "schema_old": False, "reasons": Counter()}
    assert _bettable_label(g) == "观察·分歧"


def test_plain_watch():
    g = {"bettable_ever": False, "gated_ever": False, "schema_old": False, "reasons": Counter()}
    assert _bettable_label(g) == "观察"
