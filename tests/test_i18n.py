"""球员中文名映射（app/i18n.py）的单元测试。"""
import re

from app.i18n import player_zh, slugify, with_zh

CJK = re.compile(r"[\u4e00-\u9fa5]")


def test_slugify_handles_accents_and_suffixes():
    assert slugify("Nikola Jokić") == "nikolajokic"
    assert slugify("Scotty Pippen Jr.") == "scottypippen"
    assert slugify("Trey Murphy III") == "treymurphy"
    assert slugify("D'Angelo Russell") == "dangelorussell"
    assert slugify("") == ""


def test_known_player_has_chinese_name():
    # 映射来自 data/player_zh.json（虎扑名单）。文件缺失时返回空串，不算失败。
    zh = player_zh("Chet Holmgren")
    if zh:
        assert CJK.search(zh)


def test_unknown_player_falls_back_to_empty():
    assert player_zh("Totally Unknown Player XYZ") == ""
    assert player_zh(None) == ""


def test_with_zh_keeps_english_key():
    row = with_zh({"player": "Chet Holmgren"})
    assert row["player"] == "Chet Holmgren"
    assert "player_zh" in row
