from __future__ import annotations

import sqlite3
from datetime import datetime, timezone

from ..config import CLOSING_WINDOW_MIN
from ..ingest import service
from .. import db


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def outcome(bet: dict, home_score: int, away_score: int) -> str:
    """won / lost / push / void for a settled bet."""
    market, side, line = bet["market"], bet["side"], bet["line"]
    margin = home_score - away_score
    total = home_score + away_score

    if market == "spread":
        if line is None:
            return "void"
        diff = margin + line if side == "home" else -margin + line
        return "won" if diff > 0 else "push" if diff == 0 else "lost"
    if market == "total":
        if line is None:
            return "void"
        diff = total - line
        if side == "over":
            return "won" if diff > 0 else "push" if diff == 0 else "lost"
        return "won" if diff < 0 else "push" if diff == 0 else "lost"
    if market == "moneyline":
        if side == "home":
            return "won" if margin > 0 else "lost"
        return "won" if margin < 0 else "lost"
    return "void"


def _line_clv(bet: dict, closing_line: float | None) -> float | None:
    """线移动 CLV（单位：分），符号约定为"正数 = 下注时拿到了更好的线"。

    - 让分：下注线 − 收盘线（主 -3 vs 收盘 -5 → +2）
    - 大小分：大球 收盘线 − 下注线；小球 下注线 − 收盘线
    - 胜负：无线，返回 None
    """
    line = bet.get("line")
    if line is None or closing_line is None:
        return None
    market = bet.get("market")
    if market == "spread":
        return float(line) - float(closing_line)
    if market == "total":
        if bet.get("side") == "over":
            return float(closing_line) - float(line)
        if bet.get("side") == "under":
            return float(line) - float(closing_line)
    return None


def _closing_snapshot(
    conn: sqlite3.Connection, game_id: str, market: str, side: str, tipoff: str | None
) -> sqlite3.Row | None:
    """Latest odds snapshot for the same market/side captured before tipoff."""
    tip = _parse_ts(tipoff)
    best: sqlite3.Row | None = None
    best_ts: datetime | None = None
    for row in conn.execute(
        """
        SELECT line, price, captured_at FROM odds_snapshots
        WHERE game_id=? AND market=? AND side=? AND price IS NOT NULL
        ORDER BY id
        """,
        (game_id, market, side),
    ):
        ts = _parse_ts(row["captured_at"])
        if ts is None:
            continue
        if tip and ts > tip:
            continue
        if best is None or (best_ts is None or ts >= best_ts):
            best, best_ts = row, ts
    return best


def settle(conn: sqlite3.Connection | None = None) -> list[dict]:
    """Settle open bets on finished games; fill profit, closing line/price, CLV."""
    own = conn is None
    conn = conn or db.connect()
    try:
        rows = conn.execute(
            """
            SELECT b.*, g.home_score, g.away_score, g.tipoff, g.slate_date
            FROM bets b
            JOIN games g ON g.id = b.game_id
            WHERE b.result = 'open'
              AND g.status = 'STATUS_FINAL'
              AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL
            """
        ).fetchall()

        settled: list[dict] = []
        for row in rows:
            bet = dict(row)
            result = outcome(bet, int(bet["home_score"]), int(bet["away_score"]))
            if result == "won":
                profit = bet["stake"] * (bet["price"] - 1.0)
            elif result == "lost":
                profit = -bet["stake"]
            else:
                profit = 0.0

            closing = _closing_snapshot(
                conn, bet["game_id"], bet["market"], bet["side"], bet["tipoff"]
            )
            closing_line = closing["line"] if closing else None
            closing_price = closing["price"] if closing else None
            clv = None
            if closing_price and closing_price > 1.0 and bet["price"] > 1.0:
                clv = bet["price"] / closing_price - 1.0
            clv_line = _line_clv(bet, closing_line)

            conn.execute(
                """
                UPDATE bets
                SET result=?, profit=?, closing_line=?, closing_price=?, clv=?, clv_line=?
                WHERE id=?
                """,
                (result, profit, closing_line, closing_price, clv, clv_line, bet["id"]),
            )
            settled.append(
                {
                    "bet_id": bet["id"],
                    "game_id": bet["game_id"],
                    "market": bet["market"],
                    "side": bet["side"],
                    "line": bet["line"],
                    "price": bet["price"],
                    "result": result,
                    "profit": profit,
                    "closing_line": closing_line,
                    "closing_price": closing_price,
                    "clv": clv,
                    "clv_line": clv_line,
                }
            )
        conn.commit()
        settle_signal_log(conn)
        return settled
    finally:
        if own:
            conn.close()


def settle_signal_log(conn: sqlite3.Connection) -> int:
    """回填 signal_log 的收盘价与结果（1u 研究口径），返回更新行数。

    信号日志记录的是决策时刻；这里在比赛结束后统一结算，供离线回测使用。
    收盘价取「开赛前最后一笔」同市场同侧快照，与真实注单的 CLV 口径一致。
    """
    rows = conn.execute(
        """
        SELECT s.*, g.home_score, g.away_score, g.tipoff
        FROM signal_log s
        JOIN games g ON g.id = s.game_id
        WHERE s.result IS NULL
          AND g.status = 'STATUS_FINAL'
          AND g.home_score IS NOT NULL AND g.away_score IS NOT NULL
        """
    ).fetchall()
    updated = 0
    for row in rows:
        bet = {"market": row["market"], "side": row["side"], "line": row["line"]}
        result = outcome(bet, int(row["home_score"]), int(row["away_score"]))
        closing = _closing_snapshot(
            conn, row["game_id"], row["market"], row["side"], row["tipoff"]
        )
        closing_line = closing["line"] if closing else None
        closing_price = closing["price"] if closing else None
        price = float(row["price_best"] or 0)
        if result == "won":
            profit = price - 1.0 if price > 1.0 else None
        elif result == "lost":
            profit = -1.0
        else:
            profit = 0.0
        conn.execute(
            """
            UPDATE signal_log
            SET result=?, profit=?, price_closing=?, closing_line=?
            WHERE id=?
            """,
            (result, profit, closing_price, closing_line, row["id"]),
        )
        updated += 1
    conn.commit()
    return updated


def capture_closing(
    conn: sqlite3.Connection | None = None, window_minutes: float | None = None
) -> list[dict]:
    """Re-fetch odds for games starting within the window (closing lines)."""
    own = conn is None
    conn = conn or db.connect()
    window = window_minutes if window_minutes is not None else CLOSING_WINDOW_MIN
    now = datetime.now(timezone.utc)
    try:
        rows = conn.execute(
            "SELECT id, slate_date, tipoff FROM games WHERE status != 'STATUS_FINAL'"
        ).fetchall()
        captured: list[dict] = []
        for row in rows:
            tip = _parse_ts(row["tipoff"])
            if not tip:
                continue
            minutes = (tip - now).total_seconds() / 60.0
            if 0 <= minutes <= window:
                inserted = service.refresh_game_odds(conn, row["id"], row["slate_date"])
                captured.append(
                    {"game_id": row["id"], "tipoff": row["tipoff"], "odds_inserted": inserted}
                )
        conn.commit()
        return captured
    finally:
        if own:
            conn.close()


LIVE_NOTE_TAG = "滚球"
BACKFILL_NOTE_TAG = "回填"
SIM_NOTE_TAG = "模拟"


def bet_kind(bet: dict) -> str:
    """区分注单来源：赛前信号 / 滚球 / 历史回填（含自动模拟）。

    - 滚球：赛时面板「记一笔」下的，备注含「滚球」
    - 回填：`scripts/backfill_bets.py` 用历史信号补的，备注以「回填」开头
    - 模拟：`app/jobs/simulate.py` 赛前自动先填的可下注信号（备注以「模拟」开头）
    - 赛前：其余（真实跟信号记的）
    回填/模拟都是信号跟踪样本，必须和真实决策分开统计。
    """
    note = bet.get("note") or ""
    if LIVE_NOTE_TAG in note:
        return "live"
    if BACKFILL_NOTE_TAG in note or SIM_NOTE_TAG in note:
        return "backfill"
    return "pregame"


def bet_source(bet: dict) -> str:
    """两种来源：真实下注 vs 信号模拟。

    - `mine`：你真实记录过的注单（跟信号记一笔 / 手动录入 / 滚球）
    - `sim`：`scripts/backfill_bets.py` 用历史信号回填的模拟注单
    """
    return "sim" if bet_kind(bet) == "backfill" else "mine"


def _stats(rows: list[dict]) -> dict:
    """一组已结算注单的统计（含分市场拆分）。"""
    decided = [bet for bet in rows if bet["result"] in ("won", "lost", "push")]
    wins = sum(1 for bet in decided if bet["result"] == "won")
    losses = sum(1 for bet in decided if bet["result"] == "lost")
    pushes = sum(1 for bet in decided if bet["result"] == "push")
    stake = sum(bet["stake"] for bet in decided)
    profit = sum(bet["profit"] or 0.0 for bet in decided)
    clvs = [bet["clv"] for bet in decided if bet["clv"] is not None]
    clv_lines = [bet["clv_line"] for bet in decided if bet.get("clv_line") is not None]

    by_market: dict[str, dict] = {}
    for market in sorted({bet["market"] for bet in decided}):
        mrows = [bet for bet in decided if bet["market"] == market]
        m_stake = sum(bet["stake"] for bet in mrows)
        m_profit = sum(bet["profit"] or 0.0 for bet in mrows)
        m_clv = [bet["clv"] for bet in mrows if bet["clv"] is not None]
        m_clv_line = [bet["clv_line"] for bet in mrows if bet.get("clv_line") is not None]
        by_market[market] = {
            "n": len(mrows),
            "stake": m_stake,
            "profit": m_profit,
            "roi": (m_profit / m_stake) if m_stake else None,
            "avg_clv": (sum(m_clv) / len(m_clv)) if m_clv else None,
            "avg_clv_line": (sum(m_clv_line) / len(m_clv_line)) if m_clv_line else None,
        }

    return {
        "settled": len(decided),
        "wins": wins,
        "losses": losses,
        "pushes": pushes,
        "hit_rate": (wins / (wins + losses)) if (wins + losses) else None,
        "stake": stake,
        "profit": profit,
        "roi": (profit / stake) if stake else None,
        "clv_n": len(clvs),
        "avg_clv": (sum(clvs) / len(clvs)) if clvs else None,
        "clv_positive_rate": (sum(1 for value in clvs if value > 0) / len(clvs)) if clvs else None,
        "clv_line_n": len(clv_lines),
        "avg_clv_line": (sum(clv_lines) / len(clv_lines)) if clv_lines else None,
        "clv_line_positive_rate": (
            sum(1 for value in clv_lines if value > 0) / len(clv_lines)
        ) if clv_lines else None,
        "by_market": by_market,
    }


def summarize(conn: sqlite3.Connection) -> dict:
    settled = [
        dict(row)
        for row in conn.execute("SELECT * FROM bets WHERE result != 'open'")
    ]
    open_rows = [
        dict(row) for row in conn.execute("SELECT * FROM bets WHERE result = 'open'")
    ]

    result = _stats(settled)
    result["open"] = len(open_rows)

    # 赛前 / 滚球 / 回填（细分）
    by_kind: dict[str, dict] = {}
    for kind in ("pregame", "live", "backfill"):
        rows = [bet for bet in settled if bet_kind(bet) == kind]
        stats = _stats(rows)
        stats["open"] = sum(1 for bet in open_rows if bet_kind(bet) == kind)
        by_kind[kind] = stats
    result["by_kind"] = by_kind

    # 真实 vs 模拟（主口径）：模拟 = 回填，真实 = 其余
    by_source: dict[str, dict] = {}
    for source in ("mine", "sim"):
        rows = [bet for bet in settled if bet_source(bet) == source]
        stats = _stats(rows)
        stats["open"] = sum(1 for bet in open_rows if bet_source(bet) == source)
        by_source[source] = stats
    result["by_source"] = by_source
    return result
