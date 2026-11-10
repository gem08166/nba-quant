from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

from . import db
from .config import DB_PATH, REQUEST_SLEEP
from .dates import current_slate_date
from .ingest.service import ingest_date
from .jobs import backtest, notify, settle, watch as watch_job
from .jobs.predict import predict_date
from .models import rapm, ratings


def _daterange(start: str, end: str):
    current = date.fromisoformat(start)
    last = date.fromisoformat(end)
    while current <= last:
        yield current.isoformat()
        current += timedelta(days=1)


def cmd_init(_: argparse.Namespace) -> None:
    db.init_db()
    print(f"DB ready: {DB_PATH}")


def cmd_ingest(args: argparse.Namespace) -> None:
    stats = ingest_date(args.date, with_injuries=not args.no_injuries)
    print(stats)


def cmd_backfill(args: argparse.Namespace) -> None:
    total = {"games": 0, "odds": 0, "injuries": 0, "errors": 0}
    days = 0
    for slate_date in _daterange(args.start, args.end):
        days += 1
        try:
            stats = ingest_date(slate_date, with_injuries=False)
        except Exception as exc:  # noqa: BLE001
            print(f"{slate_date}: FAIL {exc}")
            continue
        for key in total:
            total[key] += stats.get(key, 0)
        if days % 10 == 0:
            print(f"{slate_date}: games={total['games']} odds={total['odds']}")
        time.sleep(REQUEST_SLEEP)
    print(f"done. days={days} {total}")


def cmd_train(_: argparse.Namespace) -> None:
    conn = db.connect()
    try:
        model = ratings.train_and_save(conn)
        print(
            f"model #{model['n_games']} games | sigma_margin={model['sigma_margin']:.2f} "
            f"sigma_total={model['sigma_total']:.2f} | elo_coef={model['margin_elo']:.3f} "
            f"rest_coef={model['margin_rest']:.3f}"
        )
    finally:
        conn.close()


def cmd_rapm_train(_: argparse.Namespace) -> None:
    conn = db.connect()
    try:
        model = rapm.fit(conn)
        rapm.save_model(conn, model)
        print(
            f"rapm: {model['n_stints']} stints from {model['n_games']} games "
            f"(skipped {model['skipped_quality']}, lam={model['lam']:.0f}, sigma={model['sigma']:.1f})"
        )
        for label, items in (("TOP", model["top"][:10]), ("BOTTOM", model["bottom"][-10:])):
            print(f"{label} (net pts / 100 poss):")
            for p in items:
                print(f"   {p['rating']:+6.2f}  {p['name']}")
    finally:
        conn.close()


def cmd_hao_odds(args: argparse.Namespace) -> None:
    """抓好体育第二盘口写入 odds_snapshots（book='hao-tiyu'）。"""
    from .ingest import hao

    conn = db.connect()
    try:
        slate = args.date or current_slate_date()
        inserted = hao.store_nba_odds(conn, slate)
        conn.commit()
        print(f"hao-tiyu odds for {slate}: {inserted} new snapshot row(s)")
    except Exception as exc:  # noqa: BLE001
        print(f"hao odds failed: {exc}")
    finally:
        conn.close()


def cmd_live_odds(args: argparse.Namespace) -> None:
    """抓取进行中的 NBA 滚球盘口，原样写入 live_odds（研究用）。"""
    from .ingest import hao

    conn = db.connect()
    try:
        slate = args.date or current_slate_date()
        stats = hao.store_live_odds(conn, slate)
        conn.commit()
        print(f"live odds for {slate}: {stats}")
    except Exception as exc:  # noqa: BLE001
        print(f"live odds failed: {exc}")
    finally:
        conn.close()


def cmd_signal_backtest(args: argparse.Namespace) -> None:
    from .jobs import signal_backtest

    report = signal_backtest.run(start=args.start, end=args.end)
    signal_backtest.print_report(report)


def cmd_linemove(args: argparse.Namespace) -> None:
    """信号→线移动方向检验（可下注闸门的第 0 层验收）。"""
    import subprocess

    script = Path(__file__).resolve().parents[1] / "scripts" / "linemove_analysis.py"
    cmd = [sys.executable, "-X", "utf8", "-u", str(script)]
    if getattr(args, "start", None):
        cmd += ["--start", args.start]
    proc = subprocess.run(cmd, encoding="utf-8", errors="replace", capture_output=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    try:
        print(out.strip() or "(no output)")
    except UnicodeEncodeError:  # Windows GBK console
        sys.stdout.buffer.write((out.strip() + "\n").encode("utf-8", "replace"))
    raise SystemExit(proc.returncode)


def cmd_odds_api(args: argparse.Namespace) -> None:
    """The Odds API 多书源盘口入库（省额度：默认 20h 内只拉一次）。"""
    from .ingest import theodds

    conn = db.connect()
    try:
        message = theodds.store_odds_api(conn, slate_date=args.date, force=args.force)
        conn.commit()
        print(message)
    except Exception as exc:  # noqa: BLE001
        print(f"odds-api failed: {exc}")
    finally:
        conn.close()


def cmd_hoops_sync(_: argparse.Namespace) -> None:
    """HoopsAPI：映射建立 + upcoming 主盘 + 完赛收盘归档。"""
    from .ingest import hoops

    conn = db.connect()
    try:
        stats = hoops.sync_games(conn)
        conn.commit()
        print(f"hoops sync: {stats}")
        archive = hoops.archive_closing(conn)
        conn.commit()
        print(f"hoops archive: {archive}")
    except Exception as exc:  # noqa: BLE001
        print(f"hoops sync failed: {exc}")
    finally:
        conn.close()


def cmd_predict(args: argparse.Namespace) -> None:
    results = predict_date(args.date)
    print(f"predictions: {len(results)}")
    for item in results:
        print(
            f"  {item['pred_margin']:+.1f} / {item['pred_total']:.1f} "
            f"| edges={len(item['edges'])}"
        )


def cmd_refresh(args: argparse.Namespace) -> None:
    print(ingest_date(args.date, with_injuries=not args.no_injuries))
    cmd_train(args)
    results = predict_date(args.date)
    print(f"predictions: {len(results)}")
    for item in results:
        top = item["edges"][0] if item["edges"] else None
        if top:
            print(
                f"  {top['market']} {top['side']} {top['line']} @ {top['price']:.2f} "
                f"edge={top['edge']:+.1%} ev={top['ev']:+.1%}"
            )
    if not args.no_notify:
        conn = db.connect()
        try:
            items = notify.collect_signal_items(conn, args.date)
            sent = notify.send_new_signals(conn, items)
            print(f"notified: {sent} (providers: {', '.join(notify.providers_enabled()) or 'none'})")
        finally:
            conn.close()


def cmd_closing(_: argparse.Namespace) -> None:
    captured = settle.capture_closing()
    if not captured:
        print(f"no games starting within the closing window")
        return
    for item in captured:
        print(f"closing odds captured: {item['game_id']} tipoff={item['tipoff']} +{item['odds_inserted']} rows")


def cmd_settle(_: argparse.Namespace) -> None:
    settled = settle.settle()
    if not settled:
        print("no open bets on finished games")
    for item in settled:
        clv = f" clv={item['clv']:+.1%}" if item["clv"] is not None else ""
        close = f" close={item['closing_line']}@{item['closing_price']:.2f}" if item["closing_price"] else ""
        print(f"bet #{item['bet_id']}: {item['result']:4s} profit={item['profit']:+.2f}{close}{clv}")
    conn = db.connect()
    try:
        _print_summary(settle.summarize(conn))
    finally:
        conn.close()


def cmd_report(_: argparse.Namespace) -> None:
    conn = db.connect()
    try:
        _print_summary(settle.summarize(conn))
    finally:
        conn.close()


def _print_summary(summary: dict) -> None:
    print(
        f"纸面注单: 已结算 {summary['settled']} (胜{summary['wins']}/负{summary['losses']}/走{summary['pushes']})"
        f" · 未结算 {summary['open']}"
    )
    if summary["settled"]:
        print(f"投入 {summary['stake']:.2f} · 盈亏 {summary['profit']:+.2f} · ROI {summary['roi']:+.2%}")
        if summary["hit_rate"] is not None:
            print(f"命中率 {summary['hit_rate']:.1%}")
        if summary["avg_clv"] is not None:
            print(
                f"平均 CLV {summary['avg_clv']:+.2%} (n={summary['clv_n']}) · "
                f"CLV为正比例 {summary['clv_positive_rate']:.1%}"
            )
        for market, stats in summary["by_market"].items():
            roi = f"{stats['roi']:+.2%}" if stats["roi"] is not None else "—"
            clv = f"{stats['avg_clv']:+.2%}" if stats["avg_clv"] is not None else "—"
            print(f"  {market}: n={stats['n']} 盈亏 {stats['profit']:+.2f} ROI {roi} CLV {clv}")

    # 赛前 vs 滚球分开统计（滚球注单的 CLV 口径不同，不能混算）
    by_kind = summary.get("by_kind") or {}
    if by_kind:
        print("分场景：")
        for kind, label in (("pregame", "赛前"), ("live", "滚球")):
            stats = by_kind.get(kind) or {}
            if not stats.get("settled") and not stats.get("open"):
                continue
            roi = f"{stats['roi']:+.2%}" if stats.get("roi") is not None else "—"
            clv = f"{stats['avg_clv']:+.2%}" if stats.get("avg_clv") is not None else "—"
            hit = f"{stats['hit_rate']:.1%}" if stats.get("hit_rate") is not None else "—"
            print(
                f"  {label}: 已结算 {stats.get('settled', 0)} · 未结算 {stats.get('open', 0)}"
                f" · 盈亏 {stats.get('profit', 0):+.2f} · ROI {roi} · 命中 {hit} · CLV {clv}"
            )


def cmd_watch(args: argparse.Namespace) -> None:
    watch_job.run(interval=args.interval, with_ingest=not args.no_ingest, once=args.once)


def cmd_bets_sync(args: argparse.Namespace) -> None:
    import json

    from .config import WEBDAV_PASS, WEBDAV_USER
    from .jobs import betssync, webdav

    if args.check or not webdav.enabled():
        print(json.dumps({**betssync.status(), "hint": "set NBAQ_WEBDAV_USER / NBAQ_WEBDAV_PASS in .env"}, ensure_ascii=False, indent=1))
        return
    conn = db.connect()
    try:
        stats = betssync.run(
            conn,
            force_push=args.force,
            push_static=args.push_static,
            pull_only=args.pull_only,
        )
        print(json.dumps(stats, ensure_ascii=False, indent=1))
    finally:
        conn.close()


def cmd_bets_export(args: argparse.Namespace) -> None:
    from .jobs import betssync

    conn = db.connect()
    try:
        payload = betssync.export_payload(conn)
    finally:
        conn.close()
    from .timeutil import today_beijing

    out = Path(args.out or f"data/bets-export-{today_beijing()}.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"exported {payload['count']} bet(s) -> {out}")


def cmd_bets_import(args: argparse.Namespace) -> None:
    from .jobs import betssync

    path = Path(args.file)
    if not path.exists():
        raise SystemExit(f"file not found: {path}")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if args.dry_run:
        rows = payload.get("bets") if isinstance(payload, dict) else payload
        print(f"dry-run: {len(rows or [])} row(s) in {path}")
        return
    conn = db.connect()
    try:
        stats = betssync.import_rows(conn, payload)
    finally:
        conn.close()
    print(json.dumps(stats, ensure_ascii=False))


def cmd_bets_clear(args: argparse.Namespace) -> None:
    """CLI 清空台账（与 /api/bets/clear 同口径：备份先行 + 确认文本）。"""
    from .jobs import betssync

    scope = "backfill" if args.backfill_only else "all"
    word = "回填" if scope == "backfill" else "CLEAR"
    if not args.yes:
        answer = input(f"确认清空{'回填的模拟注单' if scope == 'backfill' else '全部注单'}？输入 {word} 继续：").strip()
        if answer != word:
            print("已取消")
            return
    conn = db.connect()
    try:
        if scope == "backfill":
            rows = conn.execute("SELECT * FROM bets WHERE note LIKE '回填%'").fetchall()
        else:
            rows = conn.execute("SELECT * FROM bets").fetchall()
        if rows:
            from datetime import datetime, timezone

            backup_dir = betssync.DATA_DIR / "backup"
            backup_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            (backup_dir / f"bets-clear-{ts}.json").write_text(
                json.dumps(
                    {
                        "schema": "nbaq.bets/1",
                        "cleared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                        "scope": scope,
                        "count": len(rows),
                        "bets": [dict(r) for r in rows],
                    },
                    ensure_ascii=False,
                    indent=1,
                ),
                encoding="utf-8",
            )
            print(f"备份 {len(rows)} 条 -> {backup_dir / f'bets-clear-{ts}.json'}")
        if scope == "backfill":
            cur = conn.execute("DELETE FROM bets WHERE note LIKE '回填%'")
        else:
            cur = conn.execute("DELETE FROM bets")
        conn.commit()
        try:
            from .jobs.simulate import sim_ref

            refs = [str(r["ref"] or f"srv-{r['id']}") for r in rows]
            refs += [
                sim_ref(r["game_id"], r["market"])
                for r in rows
                if "模拟" in (r["note"] or "") or "回填" in (r["note"] or "")
            ]
            tombstoned = betssync.record_deletions(refs)
            if tombstoned:
                print(f"删除墓碑已同步（{len(refs)} 条）")
        except Exception as exc:  # noqa: BLE001
            print(f"墓碑同步失败（本地删除已生效）：{exc}")
        print(f"已清空 {cur.rowcount} 条")
    finally:
        conn.close()


def cmd_notify_test(_: argparse.Namespace) -> None:
    enabled = notify.providers_enabled()
    if not enabled:
        print("no provider configured; set NBAQ_TG_TOKEN + NBAQ_TG_CHAT_ID (or NBAQ_NTFY_TOPIC)")
        return
    text = "如果你看到这条消息，NBA Quant 的推送配置成功。"
    if notify.send("NBA Quant 测试", text):
        print(f"sent via {', '.join(enabled)}")
    else:
        print("send failed; check credentials in the log above")


def cmd_backtest(args: argparse.Namespace) -> None:
    report = backtest.run(args.test_start, args.test_end)
    backtest.print_report(report)


def cmd_holdout(args: argparse.Namespace) -> None:
    """冻结 holdout：前 X% 训练 → 后 (1-X%) 一次性考试。"""
    from scripts.holdout_eval import print_report, run

    report = run(split=args.split)
    print_report(report)


def cmd_serve(args: argparse.Namespace) -> None:
    """启动 HTTP 服务。

    无控制台环境（pythonw.exe 常驻）下不能用 uvicorn.run()：它会注册
    控制台信号处理并等待 stdin，pythonw 没有 stdin → 读到 EOF 后进程
    自行退出（表现为服务"启动几秒后消失"，且没有崩溃日志）。
    改为显式构造 Server 并把 lifespan 关掉，不依赖任何控制台句柄。
    """
    import uvicorn

    config = uvicorn.Config(
        "app.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
        log_level="info",
        # 不装 stdin/stdout 监听：常驻进程不需要，且 pythonw 下会读到 EOF
        lifespan="on",
    )
    server = uvicorn.Server(config)
    # 常驻模式：忽略 Ctrl+C 之外的中断（无控制台时收到信号也不退出）
    server.install_signal_handlers = lambda: None
    server.run()


def cmd_status(_: argparse.Namespace) -> None:
    conn = db.connect()
    try:
        games = conn.execute("SELECT COUNT(*) AS n FROM games").fetchone()["n"]
        finals = conn.execute(
            "SELECT COUNT(*) AS n FROM games WHERE home_score IS NOT NULL"
        ).fetchone()["n"]
        odds = conn.execute("SELECT COUNT(*) AS n FROM odds_snapshots").fetchone()["n"]
        preds = conn.execute("SELECT COUNT(*) AS n FROM predictions").fetchone()["n"]
        models = conn.execute("SELECT COUNT(*) AS n FROM models").fetchone()["n"]
        print(f"games={games} finals={finals} odds={odds} predictions={preds} models={models}")
        last = conn.execute("SELECT * FROM models ORDER BY id DESC LIMIT 1").fetchone()
        if last:
            print(f"latest model: {last['created_at']} n={last['n_games']} sigma=({last['sigma_margin']:.2f},{last['sigma_total']:.2f})")
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="nbaq", description="Personal pregame NBA quant tool")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init", help="create the SQLite database").set_defaults(func=cmd_init)

    ingest = sub.add_parser("ingest", help="fetch one slate")
    ingest.add_argument("--date", required=True)
    ingest.add_argument("--no-injuries", action="store_true")
    ingest.set_defaults(func=cmd_ingest)

    backfill = sub.add_parser("backfill", help="fetch a date range of historical results")
    backfill.add_argument("--start", required=True)
    backfill.add_argument("--end", required=True)
    backfill.set_defaults(func=cmd_backfill)

    sub.add_parser("train", help="fit ratings from stored finals").set_defaults(func=cmd_train)

    sub.add_parser("rapm-train", help="fit player RAPM from stored play-by-play").set_defaults(func=cmd_rapm_train)

    hao = sub.add_parser("hao-odds", help="store hao-tiyu second-book odds into odds_snapshots")
    hao.add_argument("--date", default=None)
    hao.set_defaults(func=cmd_hao_odds)

    live_odds = sub.add_parser(
        "live-odds", help="snapshot in-play NBA markets into live_odds (research only)"
    )
    live_odds.add_argument("--date", default=None)
    live_odds.set_defaults(func=cmd_live_odds)

    sb = sub.add_parser("signal-backtest", help="settle historical signal edges against captured odds")
    sb.add_argument("--start", default=None)
    sb.add_argument("--end", default=None)
    sb.set_defaults(func=cmd_signal_backtest)

    lm = sub.add_parser("linemove", help="signal→line-move direction gate (bettable acceptance layer 0)")
    lm.add_argument("--start", default=None)
    lm.set_defaults(func=cmd_linemove)

    oa = sub.add_parser("odds-api", help="fetch The Odds API multi-book odds (quota-aware)")
    oa.add_argument("--date", default=None)
    oa.add_argument("--force", action="store_true", help="ignore the min-interval guard")
    oa.set_defaults(func=cmd_odds_api)

    sub.add_parser("hoops-sync", help="HoopsAPI: map games, store main odds, archive closing odds").set_defaults(func=cmd_hoops_sync)

    predict = sub.add_parser("predict", help="predict a slate and compute edges")
    predict.add_argument("--date", required=True)
    predict.set_defaults(func=cmd_predict)

    refresh = sub.add_parser("refresh", help="ingest + train + predict (+ notify)")
    refresh.add_argument("--date", required=True)
    refresh.add_argument("--no-injuries", action="store_true")
    refresh.add_argument("--no-notify", action="store_true")
    refresh.set_defaults(func=cmd_refresh)

    bt = sub.add_parser("backtest", help="walk-forward backtest")
    bt.add_argument("--test-start", default="2026-01-01")
    bt.add_argument("--test-end", default=None)
    bt.set_defaults(func=cmd_backtest)

    ho = sub.add_parser("holdout", help="frozen holdout eval: fit on the first N percent, one-shot test the rest")
    ho.add_argument("--split", type=float, default=0.7, help="train fraction (default 0.7)")
    ho.set_defaults(func=cmd_holdout)

    closing = sub.add_parser("closing", help="capture closing odds for imminent games")
    closing.set_defaults(func=cmd_closing)

    sub.add_parser("settle", help="settle open bets on finished games").set_defaults(func=cmd_settle)
    sub.add_parser("report", help="bankroll / CLV summary").set_defaults(func=cmd_report)

    watch = sub.add_parser("watch", help="long-running loop: ingest + closing + settle + notify")
    watch.add_argument("--interval", type=float, default=None)
    watch.add_argument("--no-ingest", action="store_true")
    watch.add_argument("--once", action="store_true", help="run one loop and exit (for testing)")
    watch.set_defaults(func=cmd_watch)

    sub.add_parser("notify-test", help="send a test push").set_defaults(func=cmd_notify_test)

    bs = sub.add_parser("bets-sync", help="sync the paper ledger with Jianguoyun WebDAV")
    bs.add_argument("--check", action="store_true", help="show config/coverage only")
    bs.add_argument("--force", action="store_true", help="push the ledger even if unchanged")
    bs.add_argument("--push-static", action="store_true", help="also push site/data/static.json (CI use)")
    bs.add_argument(
        "--pull-only",
        action="store_true",
        help="only pull the cloud ledger (PC dashboard mode; never pushes)",
    )
    bs.set_defaults(func=cmd_bets_sync)

    be = sub.add_parser("bets-export", help="export the ledger as a re-importable JSON file")
    be.add_argument("--out", default=None, help="output path (default data/bets-export-YYYY-MM-DD.json)")
    be.set_defaults(func=cmd_bets_export)

    bi = sub.add_parser("bets-import", help="merge a ledger JSON file (idempotent: dups are skipped)")
    bi.add_argument("--file", required=True)
    bi.add_argument("--dry-run", action="store_true", help="only count rows")
    bi.set_defaults(func=cmd_bets_import)

    bc = sub.add_parser("bets-clear", help="clear the ledger (backs up to data/backup/ first)")
    bc.add_argument("--backfill-only", action="store_true", help="only clear backfill (sim) bets")
    bc.add_argument("--yes", action="store_true", help="skip interactive confirmation")
    bc.set_defaults(func=cmd_bets_clear)

    serve = sub.add_parser("serve", help="run the web app")
    serve.add_argument("--host", default="0.0.0.0")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--reload", action="store_true")
    serve.set_defaults(func=cmd_serve)

    sub.add_parser("status", help="show database counts").set_defaults(func=cmd_status)
    return parser


def _ensure_std_streams() -> None:
    """把缺失的标准输出接上黑洞。

    以 DETACHED_PROCESS / pythonw 启动时 sys.stdout / sys.stderr 可能是 None：
    print() 会抛 AttributeError，uvicorn 初始化日志也会直接失败，
    表现为「服务启动几秒就消失且没有崩溃日志」。
    """
    import os

    for name in ("stdout", "stderr"):
        if getattr(sys, name) is None:
            setattr(sys, name, open(os.devnull, "w", encoding="utf-8"))


def main(argv: list[str] | None = None) -> None:
    _ensure_std_streams()
    parser = build_parser()
    args = parser.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
