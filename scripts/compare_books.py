"""并排对比 ESPN(DraftKings) 与 好体育 的 NBA 盘口，用于观察分歧。

只读、不落库。用法（仓库根目录）：
    python -m scripts.compare_books --date 2026-11-04
    python -m scripts.compare_books            # 默认用下一个有比赛的日期

输出每个共同比赛的：让分/大小/独赢 两家价格 + 偏差；分叉大的行会标 ⚠。
口径提醒：好体育大小分是否含加时未验证，|diff|>6 时仅作参考。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone

from app import db
from app.ingest import hao
from app.jobs.predict import latest_odds
from app.timeutil import eastern_date


def _espn_lines(rows: list[dict]) -> dict:
    """最新一条 ESPN 盘口，按 market 聚合。"""
    latest: dict[tuple[str, str], dict] = {}
    for row in rows:
        latest[(row["market"], row["side"])] = row
    spread = latest.get(("spread", "home"))
    over = latest.get(("total", "over"))
    under = latest.get(("total", "under"))
    ml_h = latest.get(("moneyline", "home"))
    ml_a = latest.get(("moneyline", "away"))
    return {
        "spread_line": spread["line"] if spread else None,
        "spread_home_price": spread["price"] if spread else None,
        "total": over["line"] if over else (under["line"] if under else None),
        "total_over_price": over["price"] if over else None,
        "ml_home": ml_h["price"] if ml_h else None,
        "ml_away": ml_a["price"] if ml_a else None,
    }


def _dev(label: str, a: float | None, b: float | None) -> str:
    if a is None or b is None:
        return f"{label}: — vs —"
    diff = a - b
    flag = " ⚠" if abs(diff) >= 2 else ""
    return f"{label}: {a:.2f} vs {b:.2f} (Δ{diff:+.1f}){flag}"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="美国比赛日 YYYY-MM-DD（默认：下一个有比赛的日期）")
    args = parser.parse_args()

    conn = db.connect()
    try:
        if args.date:
            slate = args.date
        else:
            row = conn.execute(
                """
                SELECT slate_date FROM games
                WHERE status NOT IN ('STATUS_POSTPONED','STATUS_CANCELED','STATUS_CANCELLED')
                  AND slate_date >= date('now', '-1 day')
                ORDER BY slate_date LIMIT 1
                """
            ).fetchone()
            slate = row["slate_date"] if row else datetime.now(timezone.utc).strftime("%Y-%m-%d")

        games = conn.execute(
            """
            SELECT g.id, g.slate_date, g.tipoff, g.home_id, g.away_id,
                   h.name AS home_name, h.abbrev AS home_abbrev,
                   a.name AS away_name, a.abbrev AS away_abbrev
            FROM games g JOIN teams h ON h.id=g.home_id JOIN teams a ON a.id=g.away_id
            WHERE g.slate_date=?
            ORDER BY COALESCE(g.tipoff, '')
            """,
            (slate,),
        ).fetchall()
        print(f"比赛日 {slate}: {len(games)} 场（ESPN 盘口来自本地快照）\n")

        matches = hao.fetch_nba_matches()
        target = [
            hao.match_summary(m)
            for m in matches
            if m.get("state") in ("", "not_start")
            # 美国东部比赛日：真实 America/New_York（含夏令时）
            and eastern_date(datetime.fromtimestamp(m.get("kickoffTime") or 0, tz=timezone.utc))
            == slate
        ]
        print(f"好体育当日赛程: {len(target)} 场（好体育未必挂出全部 NBA 场次）\n")

        for game in games:
            home_name, away_name = game["home_name"], game["away_name"]
            espn = _espn_lines(latest_odds(conn, game["id"]))
            match = next(
                (
                    m
                    for m in target
                    if home_name.lower() in m["home"].lower() and away_name.lower() in m["away"].lower()
                ),
                None,
            )
            print(f"== {game['away_abbrev']} @ {game['home_abbrev']}  ({game['tipoff'] or 'TBD'})")
            if match is None:
                print("   好体育无此场（或队名不匹配）\n")
                continue
            odds = hao.parse_market((hao.fetch_match_odds(match["iid"]) or {}).get("market"))
            ah = (odds.get("ah") or [{}])[0]
            ou = (odds.get("ou") or [{}])[0]
            ml = odds.get("ml") or {}

            if espn["spread_line"] is not None or ah.get("line") is not None:
                print(
                    f"   让分  ESPN {espn['spread_line']} @{espn['spread_home_price']:.2f}"
                    if espn["spread_home_price"] is not None
                    else "   让分  ESPN —"
                )
                print(
                    f"             HaoTIYU 主 {ah.get('line')} @{ah.get('home_price'):.2f}/客 @{ah.get('away_price'):.2f}"
                    if ah.get("line") is not None
                    else "             HaoTIYU 无亚盘"
                )
            if espn["total"] is not None or ou.get("line") is not None:
                print(f"   {_dev('大小', espn['total'], ou.get('line'))}   (好体育 @{ou.get('over_price')}/{ou.get('under_price')})")
            if espn["ml_home"] is not None or ml.get("home") is not None:
                print(f"   {_dev('ML主', espn['ml_home'], ml.get('home'))}")
                print(f"   {_dev('ML客', espn['ml_away'], ml.get('away'))}")
            print()
    finally:
        conn.close()


if __name__ == "__main__":
    main()
