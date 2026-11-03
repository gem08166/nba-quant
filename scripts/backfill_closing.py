"""回填 SBR 历史收盘线到 odds_snapshots（每场 8 家书源全量，book='sbr-<书名>'）。

数据源：sbrscrape（Sportsbook Review）——任意历史日期的收盘让分/大小/ML + 最终比分。
字段结构（实测）：away_spread / away_spread_odds / total / over_odds / home_ml / away_ml
均为 {书名: 值} 字典。

用途：模型 vs 收盘线的历史回测（模型 CLV）。

用法（仓库根目录）：
    python -m scripts.backfill_closing --start 2025-10-21 --end 2026-11-03
幂等：已入过库的 (日期, 书) 自动跳过。
"""
from __future__ import annotations

import argparse
import time
from datetime import date as date_cls, timedelta

from sbrscrape import Scoreboard

from app import db
from app.ingest.espn import american_to_decimal

BOOK_PREFIX = "sbr-"


def _existing_pairs(conn) -> set[tuple[str, str]]:
    rows = conn.execute(
        "SELECT substr(captured_at,1,10) AS d, book FROM odds_snapshots WHERE book LIKE 'sbr-%'"
    ).fetchall()
    return {(r["d"], r["book"]) for r in rows}


def _tipoff_approx(slate_date: str) -> str:
    """无 tipoff 时用美国晚上 8 点（ET）≈ 次日 01:00Z 作 captured_at。"""
    d = date_cls.fromisoformat(slate_date) + timedelta(days=1)
    return f"{d.isoformat()}T01:00:00+00:00"


def backfill(start: str, end: str, sleep_seconds: float = 1.5) -> dict:
    conn = db.connect()
    done = _existing_pairs(conn)
    stats = {"dates": 0, "games": 0, "rows": 0, "empty_dates": 0, "errors": 0}
    d = date_cls.fromisoformat(start)
    end_d = date_cls.fromisoformat(end)

    try:
        while d <= end_d:
            key = d.isoformat()
            try:
                sb = Scoreboard(date=d)
                games = getattr(sb, "games", None) or []
            except Exception as exc:  # noqa: BLE001
                print(f"  {key}: fetch error {exc}")
                stats["errors"] += 1
                d += timedelta(days=1)
                continue
            if not games:
                stats["empty_dates"] += 1
                time.sleep(sleep_seconds)
                d += timedelta(days=1)
                continue

            matched = 0
            rows = 0
            for g in games:
                home, away = g.get("home_team") or "", g.get("away_team") or ""
                game = conn.execute(
                    """
                    SELECT g.id, COALESCE(g.tipoff, ?) AS cap FROM games g
                    JOIN teams h ON h.id=g.home_id JOIN teams a ON a.id=g.away_id
                    WHERE g.slate_date=? AND h.name=? AND a.name=?
                    """,
                    (_tipoff_approx(key), key, home, away),
                ).fetchone()
                if not game:
                    game = conn.execute(
                        """
                        SELECT g.id, COALESCE(g.tipoff, ?) AS cap FROM games g
                        JOIN teams h ON h.id=g.home_id JOIN teams a ON a.id=g.away_id
                        WHERE g.slate_date=?
                          AND (h.name LIKE ? OR a.name LIKE ?) AND (a.name LIKE ? OR h.name LIKE ?)
                        """,
                        (_tipoff_approx(key), key, f"%{home[-8:]}%", f"%{home[-8:]}%",
                         f"%{away[-8:]}%", f"%{away[-8:]}%"),
                    ).fetchone()
                if not game:
                    continue
                matched += 1
                captured = game["cap"]

                # 每本书独立入库：book = sbr-<书名>
                spreads_a = g.get("away_spread_odds") or {}
                spreads_l = g.get("home_spread") or {}  # home 让分线（负数=主让）
                totals = g.get("total") or {}
                overs = g.get("over_odds") or {}
                unders = g.get("under_odds") or {}
                mls_h = g.get("home_ml") or {}
                mls_a = g.get("away_ml") or {}

                for book in sorted(spreads_a.keys() | totals.keys() | mls_h.keys()):
                    tag = BOOK_PREFIX + book
                    if (key, tag) in done:
                        continue
                    # 让分：home_spread = 主队让分线（-4.5=主让4.5）；价格 = home_spread_odds[book]
                    line_h = spreads_l.get(book)
                    price_home_spread = american_to_decimal(g.get("home_spread_odds", {}).get(book))
                    price_away_spread = american_to_decimal(g.get("away_spread_odds", {}).get(book))
                    if line_h is not None and price_home_spread and price_away_spread:
                        rows += _insert(conn, game["id"], tag, "spread", "home", float(line_h), price_home_spread, captured)
                        rows += _insert(conn, game["id"], tag, "spread", "away", -float(line_h), price_away_spread, captured)
                    # 大小
                    t = totals.get(book)
                    po = american_to_decimal(overs.get(book))
                    pu = american_to_decimal(unders.get(book))
                    if t is not None and po and pu:
                        rows += _insert(conn, game["id"], tag, "total", "over", float(t), po, captured)
                        rows += _insert(conn, game["id"], tag, "total", "under", float(t), pu, captured)
                    # ML
                    mh = american_to_decimal(mls_h.get(book))
                    ma = american_to_decimal(mls_a.get(book))
                    if mh and ma:
                        rows += _insert(conn, game["id"], tag, "moneyline", "home", None, mh, captured)
                        rows += _insert(conn, game["id"], tag, "moneyline", "away", None, ma, captured)
                    done.add((key, tag))

            conn.commit()
            stats["dates"] += 1
            stats["games"] += matched
            stats["rows"] += rows
            if stats["dates"] % 20 == 0:
                print(f"  {key}: cumulative games={stats['games']} rows={stats['rows']}")
            time.sleep(sleep_seconds)
            d += timedelta(days=1)
    finally:
        conn.close()
    return stats


def _insert(conn, game_id: str, book: str, market: str, side: str,
            line: float | None, price: float, captured: str) -> int:
    cur = conn.execute(
        """
        INSERT INTO odds_snapshots(game_id, book, market, side, line, price, captured_at, source)
        VALUES(?, ?, ?, ?, ?, ?, ?, 'sbr')
        """,
        (game_id, book, market, side, line, price, captured),
    )
    return cur.rowcount


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--sleep", type=float, default=1.5)
    args = parser.parse_args()
    stats = backfill(args.start, args.end, args.sleep)
    print(f"done: {stats}")


if __name__ == "__main__":
    main()
