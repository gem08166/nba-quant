"""模拟注单自动化：赛前先填 + 赛后兜底回填。

口径（与复盘页一致，只跟「可下注」信号）：
- **先填**：比赛开始前，只要出现 bettable=True 的信号就立刻记一条模拟注单
  （1u、当时报价、result=open），比赛结束后由 settle 正常结算——
  这样不依赖「知道结果再回填」，没有后视偏差。
- **兜底**：每场比赛结束后，检查最近 N 天已完赛的比赛里还有没有漏记的可下注
  信号；有就补一条（用该信号首个通过阀门的版本价），并直接按终局结算。
- **去重**：每条信号一个稳定 ref（`sim-<game|market|side|line 的 hash>`），
  CI / 本机各自先填也只会落一条；被用户清空的注单有墓碑，不会再自动长回来。
"""
from __future__ import annotations

import hashlib
import sqlite3
from datetime import date, datetime, timedelta, timezone

from .. import db

SIM_NOTE_PREFIX = "模拟"


def sim_ref(game_id: str, market: str, side: str, line) -> str:
    raw = f"{game_id}|{market}|{side}|{line}"
    return "sim-" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def ensure_sim_bets(conn: sqlite3.Connection, slate_date: str, stake: float = 2.0) -> dict:
    """赛前先填：把当前 slate 里通过阀门的信号记为未结算模拟注单。

    只处理未开赛的比赛（与推送同口径）；已开赛/已完赛的交给 backfill_finished。
    """
    from . import betssync, notify

    tombstones = betssync.load_deletions()
    items = notify.collect_signal_items(conn, slate_date, bettable_only=True)
    created = skipped = 0
    for game, edge in items:
        ref = sim_ref(game["id"], edge.get("market"), edge.get("side"), edge.get("line"))
        if ref in tombstones:
            skipped += 1
            continue
        if conn.execute("SELECT 1 FROM bets WHERE ref=?", (ref,)).fetchone():
            skipped += 1
            continue
        price = float(edge.get("price") or 0)
        if price <= 1.0:
            skipped += 1
            continue
        ev = edge.get("ev")
        note = (
            f"{SIM_NOTE_PREFIX} 可下注 · {edge.get('book') or '—'} · EV {ev:+.1%} · {slate_date}"
            if isinstance(ev, (int, float))
            else f"{SIM_NOTE_PREFIX} 可下注 · {edge.get('book') or '—'} · {slate_date}"
        )
        conn.execute(
            """
            INSERT INTO bets(game_id, market, side, line, price, stake,
                             model_prob, ev, placed_at, result, profit, note, ref)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', NULL, ?, ?)
            """,
            (
                str(game["id"]),
                edge["market"],
                edge["side"],
                edge.get("line"),
                price,
                stake,
                edge.get("model_prob") if edge.get("model_prob") is not None else 0.5,
                ev if isinstance(ev, (int, float)) else 0.0,
                _now(),
                note,
                ref,
            ),
        )
        created += 1
    conn.commit()
    return {"created": created, "skipped": skipped, "signals": len(items)}


def backfill_finished(conn: sqlite3.Connection, days: int = 3, stake: float = 2.0) -> dict:
    """赛后兜底：最近 N 天已完赛比赛里，漏记的可下注信号补成已结算模拟注单。"""
    from scripts.backfill_bets import backfill

    end = date.today().isoformat()
    start = (date.today() - timedelta(days=days)).isoformat()
    return backfill(
        conn,
        stake=stake,
        start=start,
        end=end,
        bettable_only=True,
        sim_only=True,
    )
