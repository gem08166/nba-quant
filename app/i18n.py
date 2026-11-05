"""球员中文名解析（英文 → 中文）。

映射由 `python -m scripts.build_player_zh` 从虎扑球队名单抓取后写入
`data/player_zh.json`，键是虎扑 slug（小写、去重音、去标点、去世代后缀）。
运行时只做本地查表，不联网；查不到时返回空串，由前端回退英文名。
"""
from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache

from .config import DATA_DIR

PATH = DATA_DIR / "player_zh.json"
_SUFFIXES = ("jr", "sr", "ii", "iii", "iv", "v")


def slugify(name: str) -> str:
    """English display name → 虎扑 slug 风格。

    >>> slugify("Nikola Jokić")
    'nikolajokic'
    >>> slugify("Scotty Pippen Jr.")
    'scottypippen'
    """
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[.'’\-]", " ", text.lower())
    parts = [p for p in re.split(r"\s+", text) if p]
    while len(parts) > 2 and parts[-1] in _SUFFIXES:
        parts.pop()
    return re.sub(r"[^a-z0-9]", "", "".join(parts))


@lru_cache(maxsize=1)
def mapping() -> dict[str, str]:
    try:
        payload = json.loads(PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload.get("players") or {}


def player_zh(name: str | None) -> str:
    if not name:
        return ""
    return mapping().get(slugify(name), "")


def with_zh(row: dict, key: str = "player") -> dict:
    """给一行数据补上 `<key>_zh`（原地修改并返回，方便列表推导）。"""
    row[f"{key}_zh"] = player_zh(row.get(key))
    return row


# ---------------------------------------------------------------------------
# 球队中文名（ESPN 全名 / 缩写 → 中文）
# ---------------------------------------------------------------------------

TEAM_SHORT_ZH = {
    "ATL": "老鹰", "BOS": "凯尔特人", "BKN": "篮网", "CHA": "黄蜂", "CHI": "公牛",
    "CLE": "骑士", "DAL": "独行侠", "DEN": "掘金", "DET": "活塞",
    "GS": "勇士", "GSW": "勇士", "HOU": "火箭", "IND": "步行者",
    "LAC": "快船", "LAL": "湖人", "MEM": "灰熊", "MIA": "热火", "MIL": "雄鹿",
    "MIN": "森林狼", "NO": "鹈鹕", "NOP": "鹈鹕", "NY": "尼克斯", "NYK": "尼克斯",
    "OKC": "雷霆", "ORL": "魔术", "PHI": "76人", "PHX": "太阳", "POR": "开拓者",
    "SAC": "国王", "SA": "马刺", "SAS": "马刺", "TOR": "猛龙",
    "UTAH": "爵士", "UTA": "爵士", "WSH": "奇才", "WAS": "奇才",
}

TEAM_FULL_ZH = {
    "Atlanta Hawks": "亚特兰大老鹰",
    "Boston Celtics": "波士顿凯尔特人",
    "Brooklyn Nets": "布鲁克林篮网",
    "Charlotte Hornets": "夏洛特黄蜂",
    "Chicago Bulls": "芝加哥公牛",
    "Cleveland Cavaliers": "克利夫兰骑士",
    "Dallas Mavericks": "达拉斯独行侠",
    "Denver Nuggets": "丹佛掘金",
    "Detroit Pistons": "底特律活塞",
    "Golden State Warriors": "金州勇士",
    "Houston Rockets": "休斯顿火箭",
    "Indiana Pacers": "印第安纳步行者",
    "LA Clippers": "洛杉矶快船",
    "Los Angeles Clippers": "洛杉矶快船",
    "Los Angeles Lakers": "洛杉矶湖人",
    "Memphis Grizzlies": "孟菲斯灰熊",
    "Miami Heat": "迈阿密热火",
    "Milwaukee Bucks": "密尔沃基雄鹿",
    "Minnesota Timberwolves": "明尼苏达森林狼",
    "New Orleans Pelicans": "新奥尔良鹈鹕",
    "New York Knicks": "纽约尼克斯",
    "Oklahoma City Thunder": "俄克拉荷马城雷霆",
    "Orlando Magic": "奥兰多魔术",
    "Philadelphia 76ers": "费城76人",
    "Phoenix Suns": "菲尼克斯太阳",
    "Portland Trail Blazers": "波特兰开拓者",
    "Sacramento Kings": "萨克拉门托国王",
    "San Antonio Spurs": "圣安东尼奥马刺",
    "Toronto Raptors": "多伦多猛龙",
    "Utah Jazz": "犹他爵士",
    "Washington Wizards": "华盛顿奇才",
}

# 赛况文本里的队名昵称（小写 → 中文）
TEAM_NICK_ZH = {
    "hawks": "老鹰", "celtics": "凯尔特人", "nets": "篮网", "hornets": "黄蜂",
    "bulls": "公牛", "cavaliers": "骑士", "mavericks": "独行侠", "nuggets": "掘金",
    "pistons": "活塞", "warriors": "勇士", "rockets": "火箭", "pacers": "步行者",
    "clippers": "快船", "lakers": "湖人", "grizzlies": "灰熊", "heat": "热火",
    "bucks": "雄鹿", "timberwolves": "森林狼", "pelicans": "鹈鹕", "knicks": "尼克斯",
    "thunder": "雷霆", "magic": "魔术", "76ers": "76人", "suns": "太阳",
    "trail blazers": "开拓者", "blazers": "开拓者", "kings": "国王", "spurs": "马刺",
    "raptors": "猛龙", "jazz": "爵士", "wizards": "奇才",
}

# 赛况动作短语：按顺序应用（先长后短，避免 "three point jumper" 被 "jumper" 抢先）
_PLAY_RULES: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"makes (\d+)-foot three point jumper", re.I), r"三分命中（\1英尺）"),
    (re.compile(r"misses (\d+)-foot three point jumper", re.I), r"三分不中（\1英尺）"),
    (re.compile(r"makes three point jumper", re.I), "三分命中"),
    (re.compile(r"misses three point jumper", re.I), "三分不中"),
    (re.compile(r"makes (\d+)-foot (?:two point )?jumper", re.I), r"跳投命中（\1英尺）"),
    (re.compile(r"misses (\d+)-foot (?:two point )?jumper", re.I), r"跳投不中（\1英尺）"),
    (re.compile(r"makes (?:two point )?jumper", re.I), "跳投命中"),
    (re.compile(r"misses (?:two point )?jumper", re.I), "跳投不中"),
    (re.compile(r"makes (\d+)-foot [\w\s]*jump shot", re.I), r"跳投命中（\1英尺）"),
    (re.compile(r"misses (\d+)-foot [\w\s]*jump shot", re.I), r"跳投不中（\1英尺）"),
    (re.compile(r"makes [\w\s]*jump shot", re.I), "跳投命中"),
    (re.compile(r"misses [\w\s]*jump shot", re.I), "跳投不中"),
    (re.compile(r"makes (\d+)-foot [\w\s]*layup", re.I), "上篮得分"),
    (re.compile(r"misses (\d+)-foot [\w\s]*layup", re.I), "上篮不中"),
    (re.compile(r"makes [\w\s]*layup", re.I), "上篮得分"),
    (re.compile(r"misses [\w\s]*layup", re.I), "上篮不中"),
    (re.compile(r"makes (\d+)-foot layup", re.I), "上篮得分"),
    (re.compile(r"misses (\d+)-foot layup", re.I), "上篮不中"),
    (re.compile(r"makes layup", re.I), "上篮得分"),
    (re.compile(r"misses layup", re.I), "上篮不中"),
    (re.compile(r"makes (\d+)-foot dunk", re.I), "扣篮得分"),
    (re.compile(r"misses (\d+)-foot dunk", re.I), "扣篮不中"),
    (re.compile(r"makes (?:slam )?dunk", re.I), "扣篮得分"),
    (re.compile(r"misses (?:slam )?dunk", re.I), "扣篮不中"),
    (re.compile(r"makes (\d+)-foot hook shot", re.I), "勾手命中"),
    (re.compile(r"misses (\d+)-foot hook shot", re.I), "勾手不中"),
    (re.compile(r"makes hook shot", re.I), "勾手命中"),
    (re.compile(r"misses hook shot", re.I), "勾手不中"),
    (re.compile(r"makes (\d+)-foot (?:two point )?shot", re.I), r"投篮命中（\1英尺）"),
    (re.compile(r"misses (\d+)-foot (?:two point )?shot", re.I), r"投篮不中（\1英尺）"),
    (re.compile(r"makes (?:two point )?shot", re.I), "投篮命中"),
    (re.compile(r"misses (?:two point )?shot", re.I), "投篮不中"),
    (re.compile(r"makes free throw (\d+) of (\d+)", re.I), r"罚球 \1/\2 命中"),
    (re.compile(r"misses free throw (\d+) of (\d+)", re.I), r"罚球 \1/\2 不中"),
    (re.compile(r"defensive team rebound", re.I), "球队防守篮板"),
    (re.compile(r"offensive team rebound", re.I), "球队前场篮板"),
    (re.compile(r"defensive rebound", re.I), "防守篮板"),
    (re.compile(r"offensive rebound", re.I), "前场篮板"),
    (re.compile(r"team rebound", re.I), "球队篮板"),
    (re.compile(r"personal foul", re.I), "个人犯规"),
    (re.compile(r"shooting foul", re.I), "投篮犯规"),
    (re.compile(r"loose ball foul", re.I), "无球犯规"),
    (re.compile(r"offensive foul", re.I), "进攻犯规"),
    (re.compile(r"technical foul", re.I), "技术犯规"),
    (re.compile(r"coach'?s challenge", re.I), "教练挑战"),
    (re.compile(r"call overturned", re.I), "改判成功"),
    (re.compile(r"retain their timeout", re.I), "保留暂停"),
    (re.compile(r"enters the game", re.I), "上场"),
    (re.compile(r"turnover", re.I), "失误"),
    (re.compile(r"steal", re.I), "抢断"),
    (re.compile(r"block", re.I), "盖帽"),
    (re.compile(r"timeout", re.I), "暂停"),
    (re.compile(r"jump ball", re.I), "跳球"),
]

_LEAD_NAME = re.compile(r"^([A-Z][\w.'\-]*(?:\s+[A-Z][\w.'\-]*){0,3})\s+(.*)$")
_NICK = re.compile(
    r"\b(" + "|".join(sorted((re.escape(k) for k in TEAM_NICK_ZH), key=len, reverse=True)) + r")\b",
    re.I,
)


def team_zh(abbrev: str | None = None, name: str | None = None) -> str:
    """球队中文全名：优先按英文全名查，其次按缩写；查不到返回原名。"""
    if name and name in TEAM_FULL_ZH:
        return TEAM_FULL_ZH[name]
    if abbrev and str(abbrev).upper() in TEAM_SHORT_ZH:
        return TEAM_SHORT_ZH[str(abbrev).upper()]
    return name or abbrev or ""


def team_short_zh(abbrev: str | None = None, name: str | None = None) -> str:
    """球队中文简称（老鹰/湖人…）：优先缩写，其次全名。"""
    if abbrev and str(abbrev).upper() in TEAM_SHORT_ZH:
        return TEAM_SHORT_ZH[str(abbrev).upper()]
    if name:
        return TEAM_FULL_ZH.get(name, "").replace("队", "") or name
    return abbrev or ""


def matchup_zh(matchup: str | None) -> str:
    """'CHA @ BKN' → '黄蜂 @ 篮网'（无法解析的原样返回）。"""
    if not matchup or " @ " not in matchup:
        return matchup or ""
    away, home = matchup.split(" @ ", 1)
    return f"{team_short_zh(away) or away} @ {team_short_zh(home) or home}"


def _translate_player_prefix(text: str) -> str:
    match = _LEAD_NAME.match(text)
    if not match:
        return text
    words = match.group(1).split()
    rest = match.group(2)
    for take in range(len(words), 0, -1):
        candidate = " ".join(words[:take])
        zh = player_zh(candidate)
        if zh:
            tail = " ".join(words[take:])
            return f"{zh} {tail} {rest}".strip() if tail else f"{zh} {rest}"
    return text


def play_zh(text: str | None) -> str:
    """赛况逐条翻译：球员名 + 队名昵称 + 动作短语；无法完全匹配时保留英文残段。"""
    if not text:
        return ""
    out = _translate_player_prefix(text.strip())
    out = _NICK.sub(lambda m: TEAM_NICK_ZH[m.group(1).lower()], out)
    for pattern, repl in _PLAY_RULES:
        out = pattern.sub(repl, out)
    # 去掉多余空格与残留的英文介词
    out = re.sub(r"\s+", " ", out).strip()
    return out

