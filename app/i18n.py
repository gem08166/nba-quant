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
