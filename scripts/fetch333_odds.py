# -*- coding: utf-8 -*-
"""333 自家盘口 DOM 抓价（book='333'，source='333-dom'）。

背景：hao-tiyu 镜像 API 域名会轮换失效（2026-11-17 起 DNS 全死），而 333 是
我们实际下单的庄家，其价最贴地。本脚本用 headless Chrome 以游客身份抓
333sportEvents 早盘页的 NBA 卡片赔率，写入 odds_snapshots 作为第二 book。

游客可浏览盘口，无需登录态（verified 2026-11-17/18）。

流程：
1. 对北京「今天」「明天」两个日期，直达联赛视图 URL（tid 7067 = NBA）：
   /sportEvents/early/basketball/interval/<yyyymmdd>/7067
2. 等卡片渲染后读 DOM：data-testid `simple-handicap-layout-basketball-*`
   + `oddsBtn-2|<eventId>|<market>|<side>|0`（ah|h 的 label 即主队让线）
3. 中文队名 → 英文映射，按「北京日期对应的 UTC 窗口」匹配 games
4. 经 espn_service.insert_odds 入库（报价没变只更新 last_seen_at）

调度：NBA-333-Odds 计划任务每 30 分钟调用一次（pythonw，SYSTEM）。
日志：data/333-odds.log。所有失败只记日志退出，绝不影响主流程。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BEIJING = timezone(timedelta(hours=8))
WEB = "https://333fzwno98.app"
NBA_TID = 7067
LOG = ROOT / "data" / "333-odds.log"

# 333 中文队名 → 英文关键字（用于对 DB 里 ESPN 英文队名做 substring 匹配）
ZH2EN = {
    "亚特兰大老鹰": "hawks",
    "波士顿凯尔特人": "celtics",
    "布鲁克林篮网": "nets",
    "夏洛特黄蜂": "hornets",
    "芝加哥公牛": "bulls",
    "克利夫兰骑士": "cavaliers",
    "克里夫兰骑士": "cavaliers",
    "达拉斯独行侠": "mavericks",
    "丹佛掘金": "nuggets",
    "底特律活塞": "pistons",
    "金州勇士": "warriors",
    "休斯顿火箭": "rockets",
    "印第安纳步行者": "pacers",
    "洛杉矶快船": "clippers",
    "洛杉矶湖人": "lakers",
    "孟菲斯灰熊": "grizzlies",
    "迈阿密热火": "heat",
    "密尔沃基雄鹿": "bucks",
    "明尼苏达森林狼": "timberwolves",
    "新奥尔良鹈鹕": "pelicans",
    "纽约尼克斯": "knicks",
    "俄克拉荷马城雷霆": "thunder",
    "俄克拉荷马雷霆": "thunder",
    "奥兰多魔术": "magic",
    "费城76人": "76ers",
    "费城 76人": "76ers",
    "菲尼克斯太阳": "suns",
    "波特兰开拓者": "trail blazers",
    "萨克拉门托国王": "kings",
    "圣安东尼奥马刺": "spurs",
    "多伦多猛龙": "raptors",
    "犹他爵士": "jazz",
    "华盛顿奇才": "wizards",
}

# 在页面里等卡片渲染并提取（一个 evaluate 内完成，避免多次往返）
EXTRACT_JS = r"""
(async () => {
  const sleep = (ms) => new Promise(r => setTimeout(r, ms));
  for (let i = 0; i < 30; i++) {
    const cards = document.querySelectorAll('[data-testid^="simple-handicap-layout-basketball-"]');
    let filled = 0;
    for (const c of cards) {
      const h = c.querySelector('[data-testid="sport-event-card-home-name"]');
      const a = c.querySelector('[data-testid="sport-event-card-away-name"]');
      if (h && h.innerText.trim() && a && a.innerText.trim()) filled++;
    }
    if (cards.length && filled === cards.length) break;
    await sleep(1500);
  }
  const cards = document.querySelectorAll('[data-testid^="simple-handicap-layout-basketball-"]');
  const out = [];
  for (const card of cards) {
    const home = card.querySelector('[data-testid="sport-event-card-home-name"]');
    const away = card.querySelector('[data-testid="sport-event-card-away-name"]');
    const btns = card.querySelectorAll('[data-testid^="oddsBtn-"]');
    const odds = [];
    for (const b of btns) {
      const parts = (b.innerText || "").trim().split("\n").map(s => s.trim());
      odds.push({ tid: b.getAttribute("data-testid"), label: parts[0] || "", price: parts[1] || "" });
    }
    out.push({
      home: home ? home.innerText.trim() : null,
      away: away ? away.innerText.trim() : null,
      odds,
      text: (card.innerText || "").slice(0, 120),
    });
  }
  return JSON.stringify(out);
})()
"""


def _log(msg: str) -> None:
    line = f"{datetime.now(BEIJING).strftime('%m-%d %H:%M:%S')} {msg}"
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    if sys.stdout is not None:
        try:
            print(line, flush=True)
        except OSError:
            pass


def _parse_price(v: str) -> float | None:
    try:
        p = float(v)
    except (TypeError, ValueError):
        return None
    return p if 1.0 < p < 100.0 else None


def _parse_line(v: str) -> float | None:
    m = re.match(r"^([+-]?\d+(?:\.\d+)?)$", v.strip())
    return float(m.group(1)) if m else None


def card_to_rows(card: dict) -> list[dict]:
    """把一张 333 卡片的按钮解析成标准盘口行（ah|h label = 主队让线）。"""
    home, away = card.get("home"), card.get("away")
    if not home or not away:
        return []
    ah_h = ah_a = ou_ov = ou_ud = ml_h = ml_a = None
    ou_line: float | None = None
    for b in card.get("odds", []):
        parts = (b.get("tid") or "").split("|")
        if len(parts) < 5 or parts[0] != "oddsBtn-2":
            continue
        market, side = parts[2], parts[3]
        price = _parse_price(b.get("price", ""))
        if price is None:
            continue
        if market == "ah" and side == "h":
            line = _parse_line(b.get("label", ""))
            if line is not None:
                ah_h = (line, price)
        elif market == "ah" and side == "a":
            line = _parse_line(b.get("label", ""))
            if line is not None:
                ah_a = (line, price)
        elif market == "ou" and side == "ov":
            m = re.match(r"^大\s*(\d+(?:\.\d+)?)$", b.get("label", ""))
            if m and price:
                ou_line, ou_ov = float(m.group(1)), price
        elif market == "ou" and side == "ud":
            if price and b.get("label", "").startswith("小"):
                ou_ud = price
        elif market == "ml" and side == "h":
            ml_h = price
        elif market == "ml" and side == "a":
            ml_a = price

    rows: list[dict] = []
    # ah 两边取主队线口径（ah|h 即主队线），若只有 ah|a 的 label 可用则推回主队线
    line, h_price = ah_h or (None, None)
    if line is None and ah_a:
        line, _a_price = ah_a
        line = -line
    if line is not None:
        rows.append({"book": "333", "market": "spread", "side": "home", "line": line, "price": h_price, "source": "333-dom"})
        a_price = ah_a[1] if ah_a else None
        rows.append({"book": "333", "market": "spread", "side": "away", "line": -line, "price": a_price, "source": "333-dom"})
    if ou_line is not None and (ou_ov or ou_ud):
        rows.append({"book": "333", "market": "total", "side": "over", "line": ou_line, "price": ou_ov, "source": "333-dom"})
        rows.append({"book": "333", "market": "total", "side": "under", "line": ou_line, "price": ou_ud, "source": "333-dom"})
    if ml_h and ml_a:
        # 与 hao 同款交叉校验：主队让分（line<0）时主 ML 必须更便宜，否则交换
        if line is not None and (line < 0) != (ml_h < ml_a):
            ml_h, ml_a = ml_a, ml_h
        rows.append({"book": "333", "market": "moneyline", "side": "home", "line": None, "price": ml_h, "source": "333-dom"})
        rows.append({"book": "333", "market": "moneyline", "side": "away", "line": None, "price": ml_a, "source": "333-dom"})
    # 只要有任一侧价格缺失就不落行（333 卡片实际总是成对出现，此为兜底）
    rows = [r for r in rows if r["price"] is not None]
    return rows


def _launch(pw):
    """SYSTEM 会话看不到 per-user Chrome，按候选链找可用的浏览器。"""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        str(Path.home() / "AppData/Local/Google/Chrome/Application/chrome.exe"),
        r"C:\Users\Administrator\AppData\Local\Google\Chrome\Application\chrome.exe",
    ]
    for exe in candidates:
        if os.path.isfile(exe):
            try:
                return pw.chromium.launch(headless=True, executable_path=exe)
            except Exception as exc:  # noqa: BLE001
                _log(f"chrome launch via {exe} failed: {exc}")
    try:
        return pw.chromium.launch(channel="chrome", headless=True)
    except Exception as exc:  # noqa: BLE001
        _log(f"channel=chrome failed ({exc}), trying bundled chromium")
        return pw.chromium.launch(headless=True)


def _collect_cards(beijing_date: str) -> list[dict]:
    """游客身份抓某北京日期的 NBA 早盘卡片。"""
    from playwright.sync_api import sync_playwright

    ymd = beijing_date.replace("-", "")
    url = f"{WEB}/sportEvents/early/basketball/interval/{ymd}/{NBA_TID}"
    with sync_playwright() as p:
        browser = _launch(p)
        context = browser.new_context(
            viewport={"width": 480, "height": 960},
            is_mobile=True,
            has_touch=True,
            user_agent="Mozilla/5.0 (Linux; Android 13; Pixel 7) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0.0.0 Mobile Safari/537.36",
            locale="zh-CN",
        )
        try:
            page = context.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=45000)
            raw = page.evaluate(EXTRACT_JS)
            return json.loads(raw)
        finally:
            context.close()
            browser.close()


def _games_in_window(conn, beijing_date: str) -> list:
    """北京日期 D 的比赛 = UTC 窗口 [D-1 16:00Z, D 16:00Z)（NBA 全部开球时段）。"""
    d = datetime.strptime(beijing_date, "%Y-%m-%d").replace(tzinfo=BEIJING)
    start = d.astimezone(timezone.utc)  # 北京 D 00:00 = UTC (D-1) 16:00
    end = start + timedelta(days=1)
    games = conn.execute(
        """
        SELECT g.id, g.tipoff, g.status, h.name AS home_name, a.name AS away_name
        FROM games g JOIN teams h ON h.id=g.home_id JOIN teams a ON a.id=g.away_id
        WHERE g.slate_date IN (?, ?)
          AND g.status NOT IN ('STATUS_POSTPONED','STATUS_CANCELED','STATUS_CANCELLED')
        """,
        ((d - timedelta(days=1)).strftime("%Y-%m-%d"), beijing_date),
    ).fetchall()
    out = []
    for g in games:
        try:
            tip = datetime.fromisoformat(str(g["tipoff"]).replace("Z", "+00:00"))
        except ValueError:
            continue
        if tip.tzinfo is None:
            tip = tip.replace(tzinfo=timezone.utc)
        if start <= tip < end:
            out.append(g)
    return out


def store_nba_odds(conn, beijing_dates: list[str]) -> int:
    """抓 333 早盘写入 odds_snapshots，返回新增行数。"""
    from app.ingest import service as espn_service

    inserted = 0
    for beijing_date in beijing_dates:
        try:
            cards = _collect_cards(beijing_date)
        except Exception as exc:  # noqa: BLE001
            _log(f"{beijing_date} capture failed: {exc}")
            continue
        if not cards:
            _log(f"{beijing_date} no cards (no games published yet?)")
            continue
        games = _games_in_window(conn, beijing_date)
        for card in cards:
            en_home = ZH2EN.get(card.get("home", "").replace(" ", ""))
            en_away = ZH2EN.get(card.get("away", "").replace(" ", ""))
            if not en_home or not en_away:
                _log(f"{beijing_date} unmapped teams: {card.get('home')} vs {card.get('away')}")
                continue
            game = next(
                (
                    g
                    for g in games
                    if en_home in g["home_name"].lower() and en_away in g["away_name"].lower()
                ),
                None,
            )
            if game is None:
                _log(f"{beijing_date} no db game for {card.get('home')} vs {card.get('away')}")
                continue
            rows = card_to_rows(card)
            n = espn_service.insert_odds(conn, game["id"], rows)
            inserted += n
            _log(
                f"{beijing_date} {game['away_name']} @ {game['home_name']}: "
                f"{len(rows)} parsed, {n} new row(s)"
            )
    return inserted


def main() -> int:
    from app import db

    now_bj = datetime.now(BEIJING)
    dates = [now_bj.strftime("%Y-%m-%d"), (now_bj + timedelta(days=1)).strftime("%Y-%m-%d")]
    conn = db.connect()
    try:
        n = store_nba_odds(conn, dates)
        conn.commit()
        _log(f"done: {n} new row(s) for {'+'.join(dates)}")
        return 0
    except Exception as exc:  # noqa: BLE001
        _log(f"fatal: {exc}")
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    # pythonw 无控制台时 sys.stdout 为 None，_log 已兜底；这里只为错误码
    sys.exit(main())
