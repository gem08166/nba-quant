"""合并「真实注单」里的重复记录（同场/同市场/同方向/同线且赔率几乎相同）。

背景：同一笔真实下注可能被手机和 PC 各记一次（ref 不同、placed_at 差几分钟），
自然键按精确时间匹配不到，台账里就出现两条。

规则（保守）：
- 只看真实注单（备注不以「回填/模拟」开头）；
- 同 (game, market, side, line)，赔率差 <= --tol（默认 0.02），且下注时间差
  <= --hours（默认 48h）视为同一笔；
- 保留注额最大的那条（并列时保留最早的），其余删除并写墓碑，防止云端合并回来。

用法：
    python -m scripts.dedupe_bets            # 只列出，不改
    python -m scripts.dedupe_bets --apply    # 执行（先备份到 data/backup/）
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.jobs import betssync

SIM_TAGS = ("回填", "模拟")


def _real_bets(conn):
    rows = conn.execute("SELECT * FROM bets ORDER BY id").fetchall()
    return [
        dict(r)
        for r in rows
        if not any(tag in (r["note"] or "") for tag in SIM_TAGS)
    ]


def _parse(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def find_duplicates(conn, tol: float = 0.02, hours: int = 48) -> list[dict]:
    groups: dict[tuple, list[dict]] = {}
    for bet in _real_bets(conn):
        key = (str(bet["game_id"]), bet["market"], bet["side"], bet["line"])
        groups.setdefault(key, []).append(bet)

    to_delete: list[dict] = []
    for key, bets in groups.items():
        if len(bets) < 2:
            continue
        bets.sort(key=lambda b: (_parse(b.get("placed_at")) or datetime.min.replace(tzinfo=timezone.utc), b["id"]))
        clusters: list[list[dict]] = []
        for bet in bets:
            price = float(bet.get("price") or 0)
            when = _parse(bet.get("placed_at"))
            placed = False
            for cluster in clusters:
                anchor = cluster[0]
                anchor_price = float(anchor.get("price") or 0)
                anchor_when = _parse(anchor.get("placed_at"))
                within_price = abs(price - anchor_price) <= tol + 1e-9
                within_time = (
                    when is not None
                    and anchor_when is not None
                    and abs(when - anchor_when) <= timedelta(hours=hours)
                )
                if within_price and within_time:
                    cluster.append(bet)
                    placed = True
                    break
            if not placed:
                clusters.append([bet])

        for cluster in clusters:
            if len(cluster) < 2:
                continue
            keep = max(cluster, key=lambda b: (float(b.get("stake") or 0), -b["id"]))
            for bet in cluster:
                if bet["id"] != keep["id"]:
                    to_delete.append(
                        {
                            "delete": bet,
                            "keep": keep,
                            "key": key,
                        }
                    )
    return to_delete


def find_sim_duplicates(conn) -> list[dict]:
    """模拟单：同场同市场只保留最早的一条（盘口漂移不重复下注）。"""
    groups: dict[tuple, list[dict]] = {}
    for row in conn.execute("SELECT * FROM bets ORDER BY id").fetchall():
        bet = dict(row)
        note = bet["note"] or ""
        if not any(tag in note for tag in SIM_TAGS):
            continue
        groups.setdefault((str(bet["game_id"]), bet["market"]), []).append(bet)

    to_delete: list[dict] = []
    for key, bets in groups.items():
        if len(bets) < 2:
            continue
        bets.sort(key=lambda b: b["id"])
        keep = bets[0]
        for bet in bets[1:]:
            to_delete.append({"delete": bet, "keep": keep, "key": key})
    return to_delete


def main() -> None:
    parser = argparse.ArgumentParser(description="合并注单里的重复记录（真实 / 模拟）")
    parser.add_argument("--apply", action="store_true", help="真正删除（默认只预览）")
    parser.add_argument("--tol", type=float, default=0.02, help="赔率差容忍（默认 0.02）")
    parser.add_argument("--hours", type=int, default=48, help="下注时间差容忍（默认 48h）")
    parser.add_argument(
        "--sim",
        action="store_true",
        help="改为清理模拟单：同场同市场只保留最早一条",
    )
    args = parser.parse_args()

    conn = db.connect()
    try:
        dupes = find_sim_duplicates(conn) if args.sim else find_duplicates(conn, tol=args.tol, hours=args.hours)
        if not dupes:
            print("没有发现重复注单")
            return
        print(f"发现 {len(dupes)} 条可合并的重复：")
        for item in dupes:
            d, k = item["delete"], item["keep"]
            print(
                f"  删 #{d['id']} ref={d['ref']} 盘口={d['line']} 赔率={d['price']:.3f} "
                f"@{d['placed_at']}  ← 保留 #{k['id']} 盘口={k['line']} 赔率={k['price']:.3f}"
            )

        if not args.apply:
            print("\n（预览模式；加 --apply 执行，删除前会自动备份 + 写墓碑）")
            return

        backup_dir = Path("data") / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        payload = {
            "schema": "nbaq.bets/1",
            "deduped_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "tol": args.tol,
            "hours": args.hours,
            "sim": args.sim,
            "count": len(dupes),
            "bets": [item["delete"] for item in dupes],
        }
        (backup_dir / f"bets-dedupe-{ts}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
        )

        refs = [str(item["delete"]["ref"] or f"srv-{item['delete']['id']}") for item in dupes]
        if args.sim:
            from app.jobs.simulate import sim_ref

            refs += [sim_ref(item["delete"]["game_id"], item["delete"]["market"]) for item in dupes]
        conn.executemany("DELETE FROM bets WHERE id=?", [(item["delete"]["id"],) for item in dupes])
        conn.commit()
        tombstoned = betssync.record_deletions(refs) if refs else 0
        print(f"\n已删除 {len(dupes)} 条（备份 bets-dedupe-{ts}.json，墓碑 {tombstoned} 条）")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
