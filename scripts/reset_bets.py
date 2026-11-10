"""清空台账（全部注单），并把模拟单的墓碑留下，防止 watch 立刻把旧模拟单补回来。

用途：用户要求「以 333 为准」时重建台账：
    python -m scripts.reset_bets            # 预览
    python -m scripts.reset_bets --apply    # 执行（先备份）

规则：
- 删除 bets 全部行（真实 + 模拟）；
- 备份到 data/backup/bets-reset-<ts>.json；
- 重写 nbaq/deleted.json：
  - 保留「模拟单」的 ref 与 `sim_ref(game, market)` → 旧模拟信号不会自动复活；
  - **移除所有 hao- 前缀墓碑** → 重新导入 333 订单时不会被墓碑拦住。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.jobs import betssync, simulate


def main() -> None:
    parser = argparse.ArgumentParser(description="清空台账（保留模拟单墓碑）")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    conn = db.connect()
    try:
        rows = [dict(r) for r in conn.execute("SELECT * FROM bets ORDER BY id")]
        sim = [
            r
            for r in rows
            if "模拟" in (r.get("note") or "") or "回填" in (r.get("note") or "")
        ]
        print(f"台账 {len(rows)} 条（真实 {len(rows) - len(sim)} · 模拟 {len(sim)}）")

        sim_refs = set()
        for r in sim:
            if r.get("ref"):
                sim_refs.add(str(r["ref"]))
            sim_refs.add(simulate.sim_ref(r["game_id"], r["market"]))
        old = betssync.load_deletions()
        kept = {r for r in old if not str(r).startswith("hao-")}

        print(f"墓碑：现有 {len(old)} 条 → 保留 {len(kept)} 条 + 新增模拟 {len(sim_refs)} 条")
        print(f"将清除所有 hao- 墓碑 {sum(1 for r in old if str(r).startswith('hao-'))} 条（允许重导 333）")

        if not args.apply:
            print("\n预览模式；加 --apply 执行（先备份）")
            return

        backup_dir = Path("data") / "backup"
        backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
        (backup_dir / f"bets-reset-{ts}.json").write_text(
            json.dumps(
                {
                    "schema": "nbaq.bets/1",
                    "reset_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "count": len(rows),
                    "bets": rows,
                },
                ensure_ascii=False,
                indent=1,
                default=str,
            ),
            encoding="utf-8",
        )
        conn.execute("DELETE FROM bets")
        conn.commit()

        final = sorted(kept | sim_refs)
        body = json.dumps(
            {
                "schema": "nbaq.bets.deleted/1",
                "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "refs": final,
            },
            ensure_ascii=False,
            indent=1,
        )
        betssync.webdav.put_text(betssync.webdav.DELETED, body)

        print(f"已清空 {len(rows)} 条；备份 bets-reset-{ts}.json；墓碑 {len(final)} 条")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
