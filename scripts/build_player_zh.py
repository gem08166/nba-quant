"""抓取虎扑 NBA 球队名单，生成英文名 → 中文名映射（data/player_zh.json）。

虎扑球员主页 URL 形如 `.../players/lukekornet-150789.html`，slug 是
"名+姓"的拼音/英文小写串，页面锚文本是中文名。据此可建立不依赖翻译
API 的稳定映射。

用法：
    python -m scripts.build_player_zh          # 抓取并写入 data/player_zh.json
    python -m scripts.build_player_zh --check  # 只报告数据库里的覆盖率
"""
from __future__ import annotations

import argparse
import html as html_lib
import json
import re
import time
import unicodedata

import httpx

from app import db
from app.config import DATA_DIR

OUT = DATA_DIR / "player_zh.json"
BASE = "https://nba.hupu.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"

PLAYER_ROW_RE = re.compile(
    r'href="https?://nba\.hupu\.com/players/([^"]+?)-\d+\.html"[^>]*>([^<]*)</a></b>\s*'
    r"<p>\(<b>([^<]+)</b>\)</p>",
    re.I,
)
TEAM_RE = re.compile(r'<a[^>]*href="https?://nba\.hupu\.com/players/([a-z0-9]+)"[^>]*>([^<]{2,12})</a>', re.I)
SUFFIXES = ("jr", "sr", "ii", "iii", "iv", "v")


def strip_tags(value: str) -> str:
    return re.sub(r"<[^>]+>", "", value).strip()


def slugify(name: str) -> str:
    """English display name → 虎扑 slug 风格（去重音、去标点、去世代后缀）。"""
    text = unicodedata.normalize("NFKD", name or "")
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[.'’\-]", " ", text.lower())
    parts = [p for p in re.split(r"\s+", text) if p]
    while len(parts) > 2 and parts[-1] in SUFFIXES:
        parts.pop()
    return re.sub(r"[^a-z0-9]", "", "".join(parts))


def fetch(client: httpx.Client, url: str, retries: int = 3) -> str:
    last: Exception | None = None
    for attempt in range(retries):
        try:
            resp = client.get(url)
            resp.raise_for_status()
            return resp.text
        except Exception as exc:  # noqa: BLE001 - 网络抖动重试
            last = exc
            time.sleep(1.0 + attempt)
    raise RuntimeError(f"fetch failed: {url}: {last}")


def team_slugs(client: httpx.Client) -> list[str]:
    html = fetch(client, f"{BASE}/players/lakers")
    slugs = sorted({m.group(1) for m in TEAM_RE.finditer(html)})
    return [s for s in slugs if s not in {"players"}]


def scrape(client: httpx.Client) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for team in team_slugs(client):
        try:
            html = fetch(client, f"{BASE}/players/{team}")
        except RuntimeError as exc:
            print(f"  ! {team}: {exc}")
            continue
        count = 0
        for match in PLAYER_ROW_RE.finditer(html):
            slug, raw, english = match.group(1), match.group(2), match.group(3)
            name_zh = strip_tags(raw)
            name_en = html_lib.unescape(strip_tags(english))
            if not name_zh or not re.search(r"[\u4e00-\u9fa5]", name_zh):
                continue
            # 优先用页面上的英文名（能正确处理 Şengün / Jokić 等非 ASCII 拼写）
            key = slugify(name_en) if name_en else slugify(slug)
            if not key:
                continue
            mapping[key] = name_zh
            count += 1
        print(f"  {team}: {count} 人")
        time.sleep(0.25)
    return mapping


def coverage(conn, mapping: dict[str, str]) -> None:
    rows = conn.execute("SELECT DISTINCT player FROM injuries").fetchall()
    names = [r["player"] for r in rows]
    hit = [n for n in names if slugify(n) in mapping]
    miss = sorted({n for n in names if slugify(n) not in mapping})
    print(f"伤停名单覆盖：{len(hit)}/{len(names)}")
    if miss:
        print("  未匹配：" + "、".join(miss[:20]))
    total = conn.execute("SELECT COUNT(*) AS n FROM players").fetchone()["n"]
    hit_p = conn.execute("SELECT name FROM players").fetchall()
    hit_p = [r["name"] for r in hit_p if slugify(r["name"]) in mapping]
    print(f"players 表覆盖：{len(hit_p)}/{total}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="只检查覆盖率，不抓取")
    args = parser.parse_args()

    conn = db.connect()
    try:
        if args.check and OUT.exists():
            data = json.loads(OUT.read_text(encoding="utf-8"))
            coverage(conn, data.get("players", {}))
            return
        with httpx.Client(headers={"User-Agent": UA}, timeout=20.0, follow_redirects=True) as client:
            mapping = scrape(client)
        if not mapping:
            raise SystemExit("没有抓到任何球员，检查网络/页面结构")
        # 保留手工修正（"manual" 段），抓取结果覆盖同名项
        manual: dict[str, str] = {}
        if OUT.exists():
            try:
                manual = json.loads(OUT.read_text(encoding="utf-8")).get("manual") or {}
            except (OSError, json.JSONDecodeError):
                manual = {}
        merged = {**mapping, **manual}
        payload = {
            "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            "source": "nba.hupu.com/players",
            "manual": dict(sorted(manual.items())),
            "players": dict(sorted(merged.items())),
        }
        OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"写出 {OUT}（{len(merged)} 人，其中手工 {len(manual)}）")
        coverage(conn, merged)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
