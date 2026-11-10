"""把 333体育（.com）近 7 天注单导入本地台账（真实注单）。

数据来源：浏览器里 /history/order 的 API 响应（settlement.data），导出成 JSON：
  [{"orderId","betTime","ante","netWin","orderStatus","result","parlay",
    "legs":[{"home","away","kick","market","betOn","k","odds","won","inplay","score"}]}]

口径：
- 单关（parlay=false 且 1 条 leg）才导入；串关跳过并计数；
- market: ah→spread / ou→total / ml→moneyline；betOn: h→home / a→away / ov→over / ud→under；
- 赔率是欧赔（1.93），直接入库；注额 = ante；盈亏 = netWinAmount（含本金盈亏）；
- 结果按 netWin 正负：>0 胜 / <0 负 / =0 走盘或取消；
- ref = `hao-<orderId>`（重复导入幂等）；
- 比赛映射：球队中文名 → 缩写；kick（北京时间）换算成 ET 比赛日，再按 slate+对阵找 game_id。

`--replace-manual`：导入时把之前手动记过的同一笔删掉（同场/同市场/同方向，
盘口差 ≤0.5、赔率差 ≤0.15、下注时间差 ≤24h），避免 333 记录和手动记录双份。
删除前备份并写墓碑。

用法：
    python -m scripts.import_333_bets --file data/333-bets.json            # 预览
    python -m scripts.import_333_bets --file data/333-bets.json --apply --replace-manual
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db, i18n
from app.jobs import betssync

BJT = timezone(timedelta(hours=8))
ET = timezone(timedelta(hours=-5))

MARKET = {"ah": "spread", "ou": "total", "ml": "moneyline"}
SIDE = {"h": "home", "a": "away", "ov": "over", "ud": "under", "un": "under"}
MARKET_ZH = {"spread": "让分", "total": "大小分", "moneyline": "胜负"}
SIDE_ZH = {"home": "主", "away": "客", "over": "大", "under": "小"}


def team_abbrev(full: str) -> str | None:
    """中文全名 → 缩写（'克里夫兰骑士' → CLE）。

    先按「后缀」匹配（避免 '菲尼克斯太阳' 被 '尼克斯' 抢走），
    后缀找不到再退回最长包含匹配。
    """
    if not full:
        return None
    name = full.replace("队", "").strip()
    suffix = [(abbrev, short) for abbrev, short in i18n._TEAM_ZH.items() if short and name.endswith(short)]
    if suffix:
        return max(suffix, key=lambda x: len(x[1]))[0]
    best = None
    for abbrev, short in i18n._TEAM_ZH.items():
        if short and short in name:
            if best is None or len(short) > len(best[1]):
                best = (abbrev, short)
    return best[0] if best else None


def parse_bj(text: str) -> datetime | None:
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=BJT)
    except (TypeError, ValueError):
        return None


def find_game(conn, home_ab, away_ab, kick_bj):
    kick = parse_bj(kick_bj)
    if not kick or not home_ab or not away_ab:
        return None
    slate = kick.astimezone(ET).date().isoformat()
    row = conn.execute(
        """
        SELECT g.id FROM games g
        JOIN teams h ON h.id = g.home_id
        JOIN teams a ON a.id = g.away_id
        WHERE g.slate_date=? AND h.abbrev=? AND a.abbrev=?
        LIMIT 1
        """,
        (slate, home_ab, away_ab),
    ).fetchone()
    return row["id"] if row else None


def to_bet(conn, rec: dict):
    """一条 333 记录 → 我们的 bets 行；无法映射返回 (None, reason)。"""
    if rec.get("parlay"):
        return None, "parlay"
    legs = rec.get("legs") or []
    if len(legs) != 1:
        return None, f"legs={len(legs)}"
    leg = legs[0]
    market = MARKET.get(leg.get("market"))
    side = SIDE.get(leg.get("betOn"))
    if not market or not side:
        return None, f"market={leg.get('market')}/{leg.get('betOn')}"
    price = float(leg.get("odds") or 0)
    stake = float(rec.get("ante") or 0)
    if price <= 1 or stake <= 0:
        return None, "bad price/stake"
    line = None
    if market != "moneyline":
        try:
            line = float(str(leg.get("k") or "").replace("+", ""))
        except ValueError:
            return None, "bad line"
    home_ab = team_abbrev(leg.get("home"))
    away_ab = team_abbrev(leg.get("away"))
    game_id = find_game(conn, home_ab, away_ab, leg.get("kick"))
    if not game_id:
        return None, f"no game ({home_ab}/{away_ab} {leg.get('kick')})"
    bet_time = parse_bj(rec.get("betTime"))
    if not bet_time:
        return None, "bad betTime"
    net = float(rec.get("netWin") or 0)
    result = "won" if net > 0 else "lost" if net < 0 else "push"
    line_text = f" {line:+g}" if market == "spread" and line is not None else (
        f" {line:g}" if line is not None else ""
    )
    prefix = "滚球" if leg.get("inplay") else "333体育"
    note = (
        f"{prefix} {MARKET_ZH[market]} {SIDE_ZH[side]}{line_text} "
        f"@{price:.2f} · {game_slate(conn, game_id)}"
    )
    return {
        "game_id": game_id,
        "market": market,
        "side": side,
        "line": line,
        "price": price,
        "stake": stake,
        "model_prob": 0.5,
        "ev": 0.0,
        "placed_at": bet_time.isoformat(timespec="seconds"),
        "result": result,
        "profit": net,
        "note": note,
        "ref": f"hao-{rec.get('orderId')}",
        "_bet_time": bet_time,
    }, None


def game_slate(conn, game_id: str) -> str:
    row = conn.execute("SELECT slate_date FROM games WHERE id=?", (game_id,)).fetchone()
    return row["slate_date"] if row else ""


def _parse_iso(value):
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def find_manual_matches(conn, bet):
    """找手动记过的同一笔（同场/同市场/同方向 + 盘口/赔率/时间接近）。"""
    rows = conn.execute(
        """
        SELECT * FROM bets
        WHERE game_id=? AND market=? AND side=?
          AND note NOT LIKE '回填%' AND note NOT LIKE '模拟%'
          AND (ref IS NULL OR ref NOT LIKE 'hao-%')
        """,
        (bet["game_id"], bet["market"], bet["side"]),
    ).fetchall()
    out = []
    for row in rows:
        cand = dict(row)
        c_line, b_line = cand.get("line"), bet.get("line")
        if (c_line is None) != (b_line is None):
            continue
        if b_line is not None and abs(float(c_line) - float(b_line)) > 0.5:
            continue
        c_price = float(cand.get("price") or 0)
        if abs(c_price - float(bet["price"])) > 0.15:
            continue
        c_time = _parse_iso(cand.get("placed_at"))
        if c_time and abs(c_time - bet["_bet_time"]) > timedelta(hours=24):
            continue
        out.append(cand)
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description="导入 333体育近 7 天注单")
    parser.add_argument("--file", default="data/333-bets.json")
    parser.add_argument("--apply", action="store_true", help="写入数据库（默认预览）")
    parser.add_argument(
        "--replace-manual",
        action="store_true",
        help="删除与 333 记录重复的手动注单（先备份 + 墓碑）",
    )
    args = parser.parse_args()

    records = json.loads(Path(args.file).read_text(encoding="utf-8"))
    conn = db.connect()
    try:
        prep, skipped = [], []
        for rec in records:
            bet, reason = to_bet(conn, rec)
            if not bet:
                skipped.append((rec.get("orderId"), reason))
                continue
            prep.append(bet)

        matches: dict[int, list[dict]] = {}
        if args.replace_manual:
            for bet in prep:
                for cand in find_manual_matches(conn, bet):
                    matches[cand["id"]] = cand
        else:
            for bet in prep:
                matches.update({c["id"]: c for c in find_manual_matches(conn, bet)})

        print(f"可导入 {len(prep)} 条；跳过 {len(skipped)} 条")
        for oid, reason in skipped:
            print(f"  跳过 {oid}: {reason}")

        dup = 0
        for cand in matches.values():
            print(
                f"  手动重复 #{cand['id']} {cand['game_id']} {cand['market']}/{cand['side']}/"
                f"{cand['line']} @{cand['price']:.2f} {cand['placed_at']}"
                + ("  → 删除" if args.replace_manual else "  → 保留（未加 --replace-manual）")
            )
            dup += 1

        if not args.apply:
            print(f"\n预览：导入 {len(prep)} 条，识别手动重复 {dup} 条。加 --apply 执行。")
            return

        backup_dir = Path("data") / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        if matches:
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            (backup_dir / f"bets-333-replaced-{ts}.json").write_text(
                json.dumps([c for c in matches.values()], ensure_ascii=False, indent=1, default=str),
                encoding="utf-8",
            )

        tombstones = []
        if args.replace_manual and matches:
            tombstones = [str(c.get("ref") or f"srv-{c['id']}") for c in matches.values()]
            conn.executemany("DELETE FROM bets WHERE id=?", [(cid,) for cid in matches])
            conn.commit()

        result = betssync.merge_rows(conn, prep, tombstones=betssync.load_deletions())
        if tombstones:
            betssync.record_deletions(tombstones)
        print(
            f"导入完成：新增 {result['added']} · 更新 {result['updated']} · "
            f"重复跳过 {result['duplicates']} · 删除手动重复 {len(matches)}"
        )
    finally:
        conn.close()


if __name__ == "__main__":
    main()
