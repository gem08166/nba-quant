from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

import httpx

from .. import db
from ..config import NTFY_SERVER, NTFY_TOPIC, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
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
    response = httpx.post(
        f"{NTFY_SERVER}/{NTFY_TOPIC}",
        content=text.encode("utf-8"),
        headers={"Title": title, "Tags": "basketball", "Priority": "default"},
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


def format_signal(game: dict, edge: dict) -> str:
    market = MARKET_LABEL.get(edge["market"], edge["market"])
    if edge["market"] == "total":
        side = "大分" if edge["side"] == "over" else "小分"
    else:
        side = game["home_abbrev"] if edge["side"] == "home" else game["away_abbrev"]
    line = ""
    if edge["market"] != "moneyline" and edge.get("line") is not None:
        value = float(edge["line"])
        line = f" {value:+.1f}"
    return (
        f"{game['away_abbrev']} @ {game['home_abbrev']} · {market} {side}{line} @ {edge['price']:.2f}\n"
        f"模型 {edge['model_prob']:.1%} vs 公允 {edge['fair_prob']:.1%}"
        f" · Edge {edge['edge']:+.1%} · EV {edge['ev']:+.1%}\n"
        f"1/4凯利 {edge['stake_pct']:.1%} · 开赛 {game.get('tipoff', '')}"
    )


def collect_signal_items(conn: sqlite3.Connection, slate_date: str) -> list[tuple[dict, dict]]:
    rows = conn.execute(
        """
        SELECT g.id, g.tipoff, h.abbrev AS home_abbrev, a.abbrev AS away_abbrev
        FROM games g
        JOIN teams h ON h.id = g.home_id
        JOIN teams a ON a.id = g.away_id
        WHERE g.slate_date = ? AND g.status != 'STATUS_FINAL'
        """,
        (slate_date,),
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
            if edge.get("signal"):
                items.append((game, edge))
    return items


def send_new_signals(conn: sqlite3.Connection, items: list[tuple[dict, dict]]) -> int:
    """Push signals that were not already sent (dedup by game/market/side/line/price)."""
    if not items:
        return 0
    db.init_db(conn)

    pending: list[tuple[dict, dict]] = []
    for game, edge in items:
        exists = conn.execute(
            """
            SELECT 1 FROM notifications
            WHERE game_id=? AND market=? AND side=? AND line IS ? AND price IS ?
            """,
            (game["id"], edge["market"], edge["side"], edge.get("line"), edge.get("price")),
        ).fetchone()
        if not exists:
            pending.append((game, edge))
    if not pending:
        return 0

    text = "\n\n".join(format_signal(game, edge) for game, edge in pending)
    title = f"NBA Quant · {len(pending)} 个正 EV 信号"
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
