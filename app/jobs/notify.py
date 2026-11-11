from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import httpx

from .. import db
from ..config import NTFY_SERVER, NTFY_TOPIC, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from ..i18n import team_zh
from .predict import latest_prediction

MARKET_LABEL = {"spread": "让分", "total": "大小分", "moneyline": "胜负"}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def providers_enabled() -> list[str]:
    enabled = []
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        enabled.append("telegram")
    if NTFY_TOPIC:
        enabled.append("ntfy")
    return enabled


def _send_telegram(text: str) -> None:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    response = httpx.post(
        url,
        json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": text,
            "disable_web_page_preview": True,
        },
        timeout=15,
    )
    response.raise_for_status()


def _send_ntfy(title: str, text: str) -> None:
    # HTTP header 只允许 latin-1：中文标题用 UTF-8 字节传（ntfy 按 UTF-8 解码）
    response = httpx.post(
        f"{NTFY_SERVER}/{NTFY_TOPIC}",
        content=text.encode("utf-8"),
        headers={
            "Title": title.encode("utf-8"),
            "Tags": "basketball",
            "Priority": "default",
        },
        timeout=15,
    )
    response.raise_for_status()


def send(title: str, text: str) -> bool:
    """Send via every configured provider. Returns True if at least one succeeded."""
    sent = False
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            _send_telegram(f"*{title}*\n\n{text}")
            sent = True
        except Exception as exc:  # noqa: BLE001
            print(f"[notify] telegram failed: {exc}")
    if NTFY_TOPIC:
        try:
            _send_ntfy(title, text)
            sent = True
        except Exception as exc:  # noqa: BLE001
            print(f"[notify] ntfy failed: {exc}")
    return sent


BEIJING_TZ = timezone(timedelta(hours=8))  # 中国无夏令时，固定 UTC+8（避免 Windows 缺 tzdata）


def _fmt_beijing(value, fmt: str = "%m-%d %H:%M") -> str:
    """把 ISO 时间转成北京时间字符串（推送文案统一用北京时间）。"""
    if not value:
        return "时间待定"
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(BEIJING_TZ).strftime(fmt)


def format_signal(game: dict, edge: dict) -> str:
    market = MARKET_LABEL.get(edge["market"], edge["market"])
    if edge["market"] == "total":
        side = "大分" if edge["side"] == "over" else "小分"
    else:
        side = team_zh(game["home_abbrev"]) if edge["side"] == "home" else team_zh(game["away_abbrev"])
    line = ""
    if edge["market"] != "moneyline" and edge.get("line") is not None:
        value = float(edge["line"])
        line = f" {value:+.1f}"
    return (
        f"✅ 可下注 · {team_zh(game['away_abbrev'])} @ {team_zh(game['home_abbrev'])}\n"
        f"{market} {side}{line} @ {edge['price']:.2f}"
        + (f"（{edge['book']}）" if edge.get("book") else "")
        + f"\n模型 {edge['model_prob']:.1%} vs 公允 {edge['fair_prob']:.1%}"
        f" · Edge {edge['edge']:+.1%} · EV {edge['ev']:+.1%}\n"
        f"1/4凯利 {edge['stake_pct']:.1%} · 开赛 {_fmt_beijing(game.get('tipoff'))}（北京时间）"
    )


def collect_signal_items(
    conn: sqlite3.Connection, slate_date: str, bettable_only: bool = True
) -> list[tuple[dict, dict]]:
    """收集待推送的信号。

    **只取未开赛的比赛**：模型是赛前模型（Elo + 攻防/节奏），不读当前比分，
    比赛一旦开始，赛前概率就没有意义了。旧版只过滤 STATUS_FINAL，
    导致进行中的比赛仍可能被推送"赛前信号"（DB 里的 signal 标记不会自动过期，
    过期只发生在 API 读取层）——这是会在滚球阶段发出误导性推送的隐患。

    **只推通过可下注闸门的信号**（bettable_only=True，默认）：
    signal 只代表"模型算出正 EV"，bettable 才代表"这个价真实可拿、共识够深"。
    软书/延迟线、共识书源不足、分歧闸门拦下、同场相关降级的，一律不推，
    免得把观察项当成下注建议。需要统计观察项时传 bettable_only=False。
    """
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    rows = conn.execute(
        """
        SELECT g.id, g.tipoff, h.abbrev AS home_abbrev, a.abbrev AS away_abbrev
        FROM games g
        JOIN teams h ON h.id = g.home_id
        JOIN teams a ON a.id = g.away_id
        WHERE g.slate_date = ?
          AND g.status IN ('STATUS_SCHEDULED', 'STATUS_PRE')
          AND (g.tipoff IS NULL OR g.tipoff > ?)
        """,
        (slate_date, now),
    ).fetchall()
    items: list[tuple[dict, dict]] = []
    for row in rows:
        prediction = latest_prediction(conn, row["id"])
        if not prediction:
            continue
        game = {
            "id": row["id"],
            "tipoff": row["tipoff"],
            "home_abbrev": row["home_abbrev"],
            "away_abbrev": row["away_abbrev"],
        }
        for edge in prediction["details"].get("edges", []):
            if not edge.get("signal"):
                continue
            if bettable_only and not edge.get("bettable"):
                continue
            items.append((game, edge))
    return items


def _already_sent(conn: sqlite3.Connection, game_id: str, market: str, side: str) -> bool:
    """这笔注单是否已经推送过（只按 同场/同市场/同方向 认身份）。"""
    return conn.execute(
        "SELECT 1 FROM notifications WHERE game_id=? AND market=? AND side=? LIMIT 1",
        (str(game_id), market, side),
    ).fetchone() is not None


def send_new_signals(conn: sqlite3.Connection, items: list[tuple[dict, dict]]) -> int:
    """Push signals that were not already sent.

    **去重口径：同场 / 同市场 / 同方向只推一次**，赔率和盘口不参与身份。

    赔率每几分钟就抖一次（1.94 → 1.93 → 1.885），盘口也会小幅漂移
    （233.5 → 233.75），这些都是同一笔注单的不同报价；旧版把 price/line
    放进唯一键，导致同一笔单被反复推送，手机上一串看起来完全一样的消息。
    不同方向（over/under、主/客）是不同注单，仍然各推一次；
    同一批 items 里的重复也在这里一并折掉。
    """
    if not items:
        return 0
    db.init_db(conn)

    pending: list[tuple[dict, dict]] = []
    seen: set[tuple[str, str, str]] = set()
    for game, edge in items:
        key = (str(game["id"]), edge["market"], edge["side"])
        if key in seen or _already_sent(conn, *key):
            continue
        seen.add(key)
        pending.append((game, edge))
    if not pending:
        return 0

    text = "\n\n".join(format_signal(game, edge) for game, edge in pending)
    title = f"NBA Quant · {len(pending)} 个可下注信号"
    if not send(title, text):
        return 0

    for game, edge in pending:
        conn.execute(
            """
            INSERT OR IGNORE INTO notifications(game_id, market, side, line, price, sent_at)
            VALUES(?, ?, ?, ?, ?, ?)
            """,
            (game["id"], edge["market"], edge["side"], edge.get("line"), edge.get("price"), _now()),
        )
    conn.commit()
    return len(pending)
