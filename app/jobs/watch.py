from __future__ import annotations

import sys
import time
from datetime import date as date_cls, timedelta

from .. import db
from ..config import WATCH_INTERVAL
from ..dates import current_slate_date
from ..ingest import service
from ..models import rapm, ratings
from . import backup, notify, settle
from .predict import predict_date

# Data health: alert when the slate looks empty longer than this many loops
# (ESPN outages / parser breakage otherwise go unnoticed for hours).
EMPTY_SLATE_ALERT_LOOPS = 6


class Tee:
    """同时写 stdout 和日志文件（watch 的运行记录落盘）。

    pythonw（隐藏窗口常驻）下 sys.stdout / sys.stderr 是 None，
    所以两处写入都要判空——否则一启动就 AttributeError。
    """

    def __init__(self, path) -> None:
        self.file = open(path, "a", encoding="utf-8")
        self.stdout = sys.stdout

    def write(self, text: str) -> None:
        if self.stdout is not None:
            try:
                self.stdout.write(text)
            except (OSError, ValueError):
                pass
        try:
            self.file.write(text)
            self.file.flush()
        except OSError:
            pass

    def flush(self) -> None:
        if self.stdout is not None:
            try:
                self.stdout.flush()
            except (OSError, ValueError):
                pass
        try:
            self.file.flush()
        except OSError:
            pass


def _alert(text: str) -> None:
    """推一条告警（Telegram/ntfy），同时打印。"""
    print(f"[watch] ALERT: {text}")
    notify.send("NBA Quant watch 告警", text)


def _notify_settlements(settled: list[dict]) -> None:
    if not settled or not notify.providers_enabled():
        return
    lines = []
    for item in settled:
        clv = f" · CLV {item['clv']:+.1%}" if item["clv"] is not None else ""
        lines.append(f"#{item['bet_id']} {item['result']} 盈亏 {item['profit']:+.2f}{clv}")
    total = sum(item["profit"] or 0.0 for item in settled)
    notify.send("NBA Quant 注单结算", "\n".join(lines) + f"\n合计 {total:+.2f}")


def run(interval: float | None = None, with_ingest: bool = True, once: bool = False) -> None:
    """Long-running loop: ingest once a day, capture closing lines, settle bets,
    re-predict and push new signals. Hardened: file log, failure alerts,
    empty-slate data-health monitoring."""
    from ..config import ROOT

    log_path = ROOT / "data" / "watch.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    sys.stdout = Tee(log_path)
    # pythonw/无控制台常驻时 traceback 也要落盘，否则崩溃无痕
    sys.stderr = sys.stdout

    interval = interval or WATCH_INTERVAL
    providers = notify.providers_enabled()
    print(f"[watch] started interval={interval:.0f}s ingest={with_ingest} notify={providers or 'off'} log={log_path}")

    last_slate: str | None = None
    loop_failures = 0
    empty_slate_loops = 0
    while True:
        slate = current_slate_date()
        if slate != last_slate:
            print(f"[watch] slate date: {slate}")
        loop_failed = False
        conn = db.connect()
        try:
            # 每轮都拉：比分/状态/盘口/伤停——这是面板与手机快照的新鲜度来源。
            # （旧版只在每个比赛日首轮 ingest，导致比赛开始后面板一直是「未开赛」）
            if with_ingest:
                try:
                    stats = service.ingest_date(slate)
                    print(f"[watch] ingest {slate}: {stats}")
                    # 数据健康：正常日子 ESPN 总有比赛；空赛程连续多轮说明接口/解析出问题
                    if not stats.get("games"):
                        empty_slate_loops += 1
                        if empty_slate_loops == EMPTY_SLATE_ALERT_LOOPS:
                            _alert(f"赛程 {slate} 连续 {empty_slate_loops} 轮拉到 0 场比赛，请检查 ESPN 接口")
                    else:
                        empty_slate_loops = 0
                except Exception as exc:  # noqa: BLE001
                    loop_failed = True
                    print(f"[watch] ingest failed: {exc}")

            if with_ingest and slate != last_slate:
                ratings.train_and_save(conn)
                last_slate = slate
                # 每日备份（放在 ingest 成功之后，快照含当天数据）
                backup_path = backup.backup_database()
                if backup_path:
                    print(f"[watch] backup: {backup_path.name}")
                # 第二盘口源（好体育）：入库 book='hao-tiyu'，失败可降级
                try:
                    from ..ingest import hao

                    hao_rows = hao.store_nba_odds(conn, slate)
                    conn.commit()
                    if hao_rows:
                        print(f"[watch] hao-tiyu odds: +{hao_rows} row(s)")
                except Exception as exc:  # noqa: BLE001
                    print(f"[watch] hao-tiyu odds skipped: {exc}")
                # The Odds API 多书源（省额度：状态文件控制，默认 20h 内只拉一次）
                try:
                    from ..ingest import theodds

                    message = theodds.store_odds_api(conn, slate_date=slate)
                    conn.commit()
                    print(f"[watch] {message}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[watch] odds-api skipped: {exc}")
                # HoopsAPI：upcoming 建映射 + 完赛归档收盘价（含 Pinnacle）
                try:
                    from ..ingest import hoops

                    stats_hoops = hoops.sync_games(conn)
                    conn.commit()
                    arch = hoops.archive_closing(conn)
                    conn.commit()
                    print(f"[watch] hoops: {stats_hoops} archived={arch}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[watch] hoops skipped: {exc}")
                # PBP for the last two slates (late West Coast games finish
                # after the slate flips; backfill_pbp is idempotent).
                try:
                    day = date_cls.fromisoformat(slate)
                    pbp_stats = service.backfill_pbp(
                        (day - timedelta(days=2)).isoformat(),
                        (day - timedelta(days=1)).isoformat(),
                        progress=False,
                    )
                    if pbp_stats["fetched"] or pbp_stats["errors"]:
                        print(f"[watch] pbp: {pbp_stats}")
                    model = rapm.fit(conn)
                    rapm.save_model(conn, model)
                    print(f"[watch] rapm: {model['n_stints']} stints from {model['n_games']} games")
                except Exception as exc:  # noqa: BLE001
                    print(f"[watch] pbp/rapm failed: {exc}")

            try:
                captured = settle.capture_closing(conn)
                if captured:
                    print(f"[watch] closing odds captured for {len(captured)} game(s)")
            except Exception as exc:  # noqa: BLE001
                print(f"[watch] closing capture failed: {exc}")

            try:
                settled = settle.settle(conn)
                for item in settled:
                    clv = f" clv={item['clv']:+.1%}" if item["clv"] is not None else ""
                    print(
                        f"[watch] settled bet #{item['bet_id']}: {item['result']} "
                        f"profit={item['profit']:+.2f}{clv}"
                    )
                _notify_settlements(settled)
            except Exception as exc:  # noqa: BLE001
                print(f"[watch] settle failed: {exc}")
                loop_failed = True

            # 台账 ↔ 坚果云（配置了 WebDAV 才跑）。PC 在线时把最新静态站也推上去，
            # 手机 5 分钟内就能看到新数据（CI 离线时也不会断供）
            try:
                from . import betssync

                import subprocess
                import sys as _sys

                subprocess.run(
                    [_sys.executable, "-m", "scripts.export_static", "--out", "site"],
                    cwd=str(ROOT), capture_output=True, timeout=600,
                )
                sync_stats = betssync.run(conn, push_static=True)
                if sync_stats.get("inbox_merged") or sync_stats.get("ledger_merged") or sync_stats.get("pushed") or sync_stats.get("static_pushed"):
                    print(f"[watch] betssync: {sync_stats}")
            except Exception as exc:  # noqa: BLE001
                print(f"[watch] betssync failed: {exc}")

            try:
                predict_date(slate)
                items = notify.collect_signal_items(conn, slate)
                sent = notify.send_new_signals(conn, items)
                if sent:
                    print(f"[watch] pushed {sent} new signal(s)")
            except Exception as exc:  # noqa: BLE001
                print(f"[watch] predict/notify failed: {exc}")
                loop_failed = True
        finally:
            conn.close()

        if loop_failed:
            loop_failures += 1
            if loop_failures == 3:
                _alert(f"watch 连续 {loop_failures} 轮出现关键步骤失败，请查看 data/watch.log")
        else:
            loop_failures = 0

        if once:
            break
        time.sleep(interval)
