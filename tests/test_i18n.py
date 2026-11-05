"""i18n：球队中文名 + 赛况翻译。"""
from app import i18n


def test_team_names():
    assert i18n.team_zh("CLE", "Cleveland Cavaliers") == "克利夫兰骑士"
    assert i18n.team_zh("GS", "Golden State Warriors") == "金州勇士"
    assert i18n.team_short_zh("OKC") == "雷霆"
    assert i18n.team_short_zh("NOP") == "鹈鹕"  # ESPN 变体
    assert i18n.team_short_zh("NYK") == "尼克斯"
    assert i18n.matchup_zh("CHA @ BKN") == "黄蜂 @ 篮网"
    assert i18n.matchup_zh("") == ""


def test_play_zh_basic_actions():
    assert i18n.play_zh("Danny Wolf defensive rebound").endswith("防守篮板")
    assert i18n.play_zh("Nets offensive team rebound") == "篮网 球队前场篮板"
    assert "罚球 1/2 不中" in i18n.play_zh("Michael Porter Jr. misses free throw 1 of 2")
    assert "三分不中（22英尺）" in i18n.play_zh("Terance Mann misses 22-foot three point jumper")
    assert "三分命中（28英尺）" in i18n.play_zh("Stephen Curry makes 28-foot three point jumper")
    assert i18n.play_zh("Moussa Diabate shooting foul").endswith("投篮犯规")


def test_play_zh_players():
    out = i18n.play_zh("LeBron James makes 2-foot layup")
    assert "詹姆斯" in out and "上篮得分" in out
    out = i18n.play_zh("Stephen Curry makes 28-foot three point jumper")
    assert "库里" in out


def test_play_zh_unknown_player_keeps_english():
    out = i18n.play_zh("Unknown Player defensive rebound")
    assert "Unknown Player" in out
    assert "防守篮板" in out
