# -*- coding: utf-8 -*-
"""导出 VPS 哨兵训练用的最小 seed（teams + games）。

为什么需要：VPS 上是空库，`predict_date()` 的评级模型需要至少 30 场已完赛
比赛才能拟合。没有 seed 时哨兵每轮都会抛
`not enough completed games to fit: 0`，而且旧版日志把该错误吞掉，
整夜 0 推送（2026-11-21 实测事故）。

用法：
    python -m scripts.make_seed                  # 写到 deploy/seed_games.sql
    python -m scripts.make_seed --out /tmp/seed.sql

VPS 侧导入：
    sqlite3 data/nba.db < /tmp/seed_games.sql    # 或 .venv/bin/python 读文件执行
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402

DEFAULT_OUT = ROOT / "deploy" / "seed_games.sql"


def build(out_path: Path) -> dict:
    """把 teams + 全部 games 导出成可重复执行的 INSERT 脚本。"""
    src = db.connect()
    tmp = sqlite3.connect(":memory:")
    try:
        for table in ("teams", "games"):
            cols = [r[1] for r in src.execute(f"PRAGMA table_info({table})")]
            tmp.execute(f"CREATE TABLE {table} ({', '.join(cols)})")

        teams = src.execute("SELECT * FROM teams").fetchall()
        games = src.execute("SELECT * FROM games ORDER BY slate_date").fetchall()

        tcols = [r[1] for r in src.execute("PRAGMA table_info(teams)")]
        gcols = [r[1] for r in src.execute("PRAGMA table_info(games)")]
        tmp.executemany(f"INSERT INTO teams VALUES ({','.join('?' * len(tcols))})", teams)
        tmp.executemany(f"INSERT INTO games VALUES ({','.join('?' * len(gcols))})", games)
        tmp.commit()

        lines = ["BEGIN;", "DELETE FROM teams;", "DELETE FROM games;"]
        lines += [s for s in tmp.iterdump() if s.startswith("INSERT INTO")]
        lines.append("COMMIT;")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return {
            "teams": len(teams),
            "games": len(games),
            "finals": sum(1 for g in games if g[gcols.index("status")] == "STATUS_FINAL"),
            "bytes": out_path.stat().st_size,
            "out": str(out_path),
        }
    finally:
        tmp.close()
        src.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="导出 VPS 哨兵训练 seed")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出 SQL 路径")
    args = parser.parse_args()
    stats = build(Path(args.out))
    print(stats)
    return 0 if stats["finals"] >= 30 else 1


if __name__ == "__main__":
    raise SystemExit(main())
