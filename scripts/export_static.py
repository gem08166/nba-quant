"""导出静态 JSON 数据（GitHub Pages / 真·无后端模式）。

直接调用 API 层函数，保证结构与 /api/* 完全一致。输出到 site/data/：
- board.json     赛前看板（当前比赛日 games+predictions+signals）+ 可用日期列表
- bets.json      收益分析（summary+curve+台账）
- research.json  回测报告（裁剪版）+ RAPM 排行 + 月度趋势 + 模型信息
- meta.json      模式元数据（生成时间、数据版）

用法：python -m scripts.export_static [--date YYYY-MM-DD] [--out site]
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from app import db, i18n
from app.api import bets_analysis, games as api_games, model_info, rapm_top, trends as api_trends
from app.config import DATA_DIR
from app.dates import current_slate_date


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", default=None, help="美国比赛日（默认 current_slate_date）")
    parser.add_argument("--out", default="site", help="输出目录（默认 site/）")
    args = parser.parse_args()

    out_dir = Path(args.out) / "data"
    out_dir.mkdir(parents=True, exist_ok=True)

    conn = db.connect()
    try:
        # 默认导出"下一个有比赛的日期"（休赛日导出空板没有意义）。
        # 注意用美国比赛日（current_slate_date，UTC-5）而不是 date('now')：
        # 北京时间早上 8 点后 UTC 已跨日，用 UTC 会把深夜还在打的今天跳过去。
        slate = args.date
        if not slate:
            anchor = current_slate_date()
            row = conn.execute(
                """
                SELECT slate_date, COUNT(*) AS n FROM games
                WHERE status NOT IN ('STATUS_POSTPONED','STATUS_CANCELED','STATUS_CANCELLED')
                  AND slate_date >= ?
                GROUP BY slate_date HAVING n > 0
                ORDER BY slate_date LIMIT 1
                """,
                (anchor,),
            ).fetchone()
            slate = row["slate_date"] if row else anchor
        board = api_games(date=slate, conn=conn)

        # 比赛详情静态化：每场附带伤停、盘口快照、RAPM 建议（/api/games/{id} 的静态等价物）
        for item in board["games"]:
            game_id = item["id"]
            item["injuries"] = [
                i18n.with_zh(dict(r))
                for r in conn.execute(
                    "SELECT id, team_id, player, status, detail, impact FROM injuries WHERE game_id=?",
                    (game_id,),
                )
            ]
            odds_rows = conn.execute(
                "SELECT book, market, side, line, price, captured_at FROM odds_snapshots WHERE game_id=? ORDER BY id",
                (game_id,),
            ).fetchall()
            latest: dict[tuple[str, str, str], dict] = {}
            for r in odds_rows:
                latest[(r["book"], r["market"], r["side"])] = dict(r)
            item["odds"] = list(latest.values())
            pred = item.get("prediction")
            if pred:
                pred_row = conn.execute(
                    "SELECT details FROM predictions WHERE game_id=? ORDER BY id DESC LIMIT 1",
                    (game_id,),
                ).fetchone()
                if pred_row:
                    try:
                        raw = json.loads(pred_row["details"]).get("injury_suggestions") or []
                        item["injury_suggestions"] = [i18n.with_zh(dict(s)) for s in raw]
                    except (TypeError, ValueError):
                        item["injury_suggestions"] = []
        # 可用日期列表（供静态模式自动跳转）
        date_rows = conn.execute(
            """
            SELECT slate_date, COUNT(*) AS n FROM games
            WHERE status NOT IN ('STATUS_POSTPONED','STATUS_CANCELED','STATUS_CANCELLED')
            GROUP BY slate_date ORDER BY slate_date
            """
        ).fetchall()
        board["dates"] = [{"date": r["slate_date"], "n": r["n"]} for r in date_rows]

        # 最近 10 天完赛比分：给静态模式的纸面注单做本地结算
        board["results"] = [
            dict(r)
            for r in conn.execute(
                """
                SELECT g.id, g.slate_date, g.status, g.home_score, g.away_score,
                       h.abbrev AS home_abbrev, a.abbrev AS away_abbrev
                FROM games g
                JOIN teams h ON h.id = g.home_id
                JOIN teams a ON a.id = g.away_id
                WHERE g.status = 'STATUS_FINAL' AND g.slate_date >= date(?, '-10 days')
                ORDER BY g.slate_date
                """,
                (slate,),
            )
        ]

        bets = bets_analysis(conn)
        trends = api_trends(conn)
        rapm_data = rapm_top(limit=200, conn=conn)
        model = model_info(conn)

        # 最近比赛日的赛后复盘包：静态模式（手机）跳到往期日期时，
        # 需要赛前预测 + 盘口 + 信号历史才能渲染完整的赛后板块
        from scripts.history_pack import build_history

        history = build_history(conn, slate)

        # 回测报告：裁剪逐场行（太大），保留 monthly/overall
        backtest = {"report": None}
        bt_path = DATA_DIR / "backtest_report.json"
        if bt_path.exists():
            full = json.loads(bt_path.read_text(encoding="utf-8"))
            rows = full.get("rows") or []
            # 只保留最近 400 场的精简行（散点图/校准用）
            trimmed = [
                {k: r[k] for k in ("date", "pred_total", "actual_total", "pred_margin",
                                    "actual_margin", "home_win_prob", "home_win")}
                for r in rows[-400:]
            ]
            full["rows"] = trimmed
            backtest = {"report": full}

        payload = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "board": board,
            "bets": bets,
            "trends": trends,
            "rapm": rapm_data,
            "model": model,
            "backtest": backtest,
            "history": history,
        }

        # 模型 vs 收盘线历史检验报告（路线 A 基准对照数据源）
        mcv_path = DATA_DIR / "model_clv_report.json"
        if mcv_path.exists():
            mcv = json.loads(mcv_path.read_text(encoding="utf-8"))
            mcv.pop("rows", None)  # 裁剪逐场明细
            payload["model_clv"] = mcv

        # 信号→线移动方向检验（可下注闸门的第 0 层验收数据）
        linemove_path = DATA_DIR / "linemove_report.json"
        if linemove_path.exists():
            lm = json.loads(linemove_path.read_text(encoding="utf-8"))
            lm.pop("rows", None)
            payload["linemove"] = lm

        # 冻结 holdout 验证（前 70% 训练 → 后 30% 一次性考试）
        holdout_path = DATA_DIR / "holdout_report.json"
        if holdout_path.exists():
            payload["holdout"] = json.loads(holdout_path.read_text(encoding="utf-8"))

        # 赛中胜率模型验收报告（研究模块：只报告校准，不产生滚球信号）
        lwp_path = DATA_DIR / "live_wp_report.json"
        if lwp_path.exists():
            payload["live_wp"] = json.loads(lwp_path.read_text(encoding="utf-8"))
        (out_dir / "static.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

        # 球员中文名映射：静态模式（手机）在浏览器里翻译赛况时需要
        player_zh_path = DATA_DIR / "player_zh.json"
        if player_zh_path.exists():
            import shutil as _shutil

            _shutil.copy2(player_zh_path, out_dir / "player_zh.json")

        # 组装完整可部署站点：web 静态文件 + data
        import shutil

        site_root = Path(args.out)
        web_src = Path(__file__).resolve().parents[1] / "app" / "web"
        static_dir = site_root / "static"
        if static_dir.exists():
            shutil.rmtree(static_dir)
        shutil.copytree(web_src, static_dir)
        for name in ("index.html", "manifest.webmanifest", "sw.js"):
            if (web_src / name).exists():
                shutil.copy2(web_src / name, site_root / name)

        print(f"exported: {out_dir / 'static.json'}")
        print(f"  board: {board['date']} {board['count']} games, signals={board['signals']}")
        print(f"  history: {len(history)} day(s) -> " + ", ".join(f"{k}({len(v)})" for k, v in history.items()))
        print(f"  bets: {bets['summary']['settled']} settled, curve={len(bets['curve'])}")
        print(f"  site ready: {site_root} (index.html + sw.js + static/ + data/)")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
