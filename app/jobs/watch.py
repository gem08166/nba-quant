from __future__ import annotations

import sys
import time
from datetime import date as date_cls, datetime, timedelta, timezone

from .. import db
from ..config import CI_TRIGGER_MINUTES, LIVE_INTERVAL, LOCAL_PUBLISH_LIVE, WATCH_INTERVAL
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
    日志文件打不开（被占用/只读/坚果云锁）时降级为只写 stdout，
    不能让常驻进程在启动阶段直接崩掉。
    """

    def __init__(self, path) -> None:
        try:
            self.file = open(path, "a", encoding="utf-8")
        except OSError:
            self.file = None
        self.stdout = sys.stdout

    def write(self, text: str) -> None:
        if self.stdout is not None:
            try:
                self.stdout.write(text)
            except (OSError, ValueError):
                pass
        if self.file is None:
            return
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
        if self.file is None:
            return
        try:
            self.file.flush()
        except OSError:
            pass


def _missing_slate_days(conn) -> list[str]:
    """有注单、但 games 表缺行的比赛日（按注单 placed_at 推断），升序返回。

    用 UTC−5 近似 ET 比赛日：凌晨 00:00–05:00Z 下的注单属于前一天的 slate
    （例：11-06T02:00Z 的比赛是 11-05 ET）。否则会去补拉一个不存在的日期。
    """
    rows = conn.execute(
        """
        SELECT DISTINCT date(b.placed_at, '-5 hours') AS slate_date
        FROM bets b LEFT JOIN games g ON g.id = b.game_id
        WHERE g.id IS NULL AND b.placed_at IS NOT NULL
        """
    ).fetchall()
    return sorted({r["slate_date"] for r in rows if r["slate_date"]})


def _repair_missing_slates(conn, ingest=service.ingest_date) -> list[str]:
    """补 ingest 缺失的 slate；修不好的（ESPN 也没这场）下次不再重试。

    注意：`service.ingest_date(slate_date)` 自己开连接，不吃 conn。
    """
    repaired: list[str] = []
    for day in _missing_slate_days(conn):
        try:
            stats = ingest(day)
            print(f"[watch] repair missing slate {day}: {stats}")
            repaired.append(day)
        except Exception as exc:  # noqa: BLE001
            print(f"[watch] repair missing slate {day} failed: {exc}")
    return repaired


def has_live_games(conn, slate: str) -> bool:
    """当前 slate 是否有进行中的比赛。

    只看当天：历史日期卡在 IN_PROGRESS 的僵尸场次不该把采样间隔永久压在
    LIVE_INTERVAL（实测 11-04 有一场 116-114 卡了一整天）。
    """
    return bool(
        conn.execute(
            "SELECT 1 FROM games WHERE slate_date = ? AND status IN "
            "('STATUS_IN_PROGRESS','STATUS_HALFTIME') LIMIT 1",
            (slate,),
        ).fetchone()
    )


def stale_slate(conn, slate: str, days: int = 7, min_age_minutes: int = 60) -> str | None:
    """最近 N 天内最早一个仍有未完赛比赛的 slate（用于修僵尸状态）。

    以该日最新一次 upsert（`games.updated_at`）做节流：刚补拉过的日期
    `min_age_minutes` 内不会再拉。这样常驻 watch 和 CI 的 `watch --once`
    （每 30 分钟一次）都不会重复打接口。
    返回 None 表示没有需要补拉的日期。
    """
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=min_age_minutes)
    row = conn.execute(
        """
        SELECT slate_date FROM games
        WHERE slate_date < ?
          AND slate_date >= date(?, ?)
          AND COALESCE(status, '') NOT IN
              ('STATUS_FINAL', 'STATUS_POSTPONED', 'STATUS_CANCELED')
        GROUP BY slate_date
        HAVING MAX(COALESCE(updated_at, '')) < ?
        ORDER BY slate_date
        LIMIT 1
        """,
        (slate, slate, f"-{days} day", cutoff.isoformat(timespec="seconds")),
    ).fetchone()
    return row["slate_date"] if row else None


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


def _rotate_log(path, max_bytes: int = 5 * 1024 * 1024, keep: int = 3) -> list[str]:
    """日志超过 max_bytes 时滚动：watch.log → watch.log.1 → .2 → .3。

    pythonw 常驻进程没有外部日志轮转，跑几个月会把 data/watch.log 写爆；
    在启动时滚一次即可（每次重启都是新文件，长期运行的单个进程也很少跨月）。
    """
    import os

    moved: list[str] = []
    try:
        if not path.exists() or path.stat().st_size <= max_bytes:
            return moved
        for i in range(keep - 1, 0, -1):
            src = path.with_name(f"{path.name}.{i}")
            dst = path.with_name(f"{path.name}.{i + 1}")
            if src.exists():
                os.replace(src, dst)
                moved.append(str(dst))
        dst = path.with_name(f"{path.name}.1")
        os.replace(path, dst)
        moved.append(str(dst))
    except OSError:
        pass
    return moved


def run(interval: float | None = None, with_ingest: bool = True, once: bool = False) -> None:
    """Long-running loop: ingest once a day, capture closing lines, settle bets,
    re-predict and push new signals. Hardened: file log, failure alerts,
    empty-slate data-health monitoring."""
    from ..config import ROOT

    log_path = ROOT / "data" / "watch.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    rotated = _rotate_log(log_path)
    sys.stdout = Tee(log_path)
    # pythonw/无控制台常驻时 traceback 也要落盘，否则崩溃无痕
    sys.stderr = sys.stdout
    if rotated:
        print(f"[watch] log rotated -> {', '.join(rotated)}")

    interval = interval or WATCH_INTERVAL
    providers = notify.providers_enabled()
    print(f"[watch] started interval={interval:.0f}s ingest={with_ingest} notify={providers or 'off'} log={log_path}")

    last_slate: str | None = None
    loop_failures = 0
    empty_slate_loops = 0
    settle_pending: list[str] = []  # 还有未结算注单的历史比赛日
    was_live = False
    last_ci_trigger = 0.0
    while True:
        slate = current_slate_date()
        if slate != last_slate:
            print(f"[watch] slate date: {slate}")
        loop_failed = False
        live_now = False
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

            # 历史比赛日的"僵尸未结算"兜底：slate 翻天后，旧日的比赛已完赛但
            # 再也不会被 ingest（只拉当日），未结算注单会永远 open。
            # 每轮把有 open 注单的历史日期补 ingest 一次（有结果时才有写入，
            # 完赛次日之后 ESPN 比分稳定，一次即可收敛）。
            try:
                pending = [
                    r["slate_date"]
                    for r in conn.execute(
                        """
                        SELECT DISTINCT g.slate_date FROM bets b
                        JOIN games g ON g.id = b.game_id
                        WHERE b.result = 'open'
                          AND g.slate_date < ?
                          AND g.status != 'STATUS_FINAL'
                        """,
                        (slate,),
                    )
                ]
                for day in pending:
                    if day not in settle_pending:
                        settle_pending.append(day)
                for day in list(settle_pending):
                    try:
                        day_stats = service.ingest_date(day)
                        settled_left = conn.execute(
                            """
                            SELECT COUNT(*) n FROM bets b
                            JOIN games g ON g.id = b.game_id
                            WHERE b.result='open' AND g.slate_date=? AND g.status='STATUS_FINAL'
                            """,
                            (day,),
                        ).fetchone()["n"]
                        print(f"[watch] ingest pending day {day}: {day_stats} open-but-final left={settled_left}")
                        if settled_left == 0:
                            settle_pending.remove(day)
                        if len(settle_pending) > 5:  # 防止极端情况无限积累
                            settle_pending.clear()
                    except Exception as exc:  # noqa: BLE001
                        print(f"[watch] ingest pending day {day} failed: {exc}")
                        break  # 单日失败本轮跳过，下轮重试
            except Exception as exc:  # noqa: BLE001
                print(f"[watch] pending-day sweep failed: {exc}")

            # 「注单存在、比赛行缺失」兜底：CI 某天持久化失败会让整个 slate 缺失
            # （实测 2026-11-05），指向那天的注单在台账里永远是裸 game_id。
            # pending-day 扫描只看已入库的比赛（JOIN games），盖不住这种情况。
            try:
                _repair_missing_slates(conn)
            except Exception as exc:  # noqa: BLE001
                print(f"[watch] missing-slate repair failed: {exc}")

            # 历史「僵尸状态」修复：比赛跨过 slate 后没人再拉，状态会永远停在
            # IN_PROGRESS（实测 11-04 有一场 116-114 卡了一整天，把采样拖在 120s）。
            # 节流在 SQL 里按 games.updated_at 做（1 小时），常驻与 --once 都安全。
            try:
                stale_day = stale_slate(conn, slate)
                if stale_day:
                    repair = service.ingest_date(stale_day)
                    print(f"[watch] repair stale slate {stale_day}: {repair}")
            except Exception as exc:  # noqa: BLE001
                print(f"[watch] stale slate repair failed: {exc}")

            # 赛中胜率轨迹（研究模块）：每轮给进行中的比赛记一行。
            # 只记录、不产生信号；未来用于与滚球盘口做校准对照。
            if with_ingest:
                try:
                    from ..models.live_wp import record_snapshot

                    wp_stats = record_snapshot(conn, slate)
                    if wp_stats.get("in_play"):
                        print(f"[watch] live_wp: {wp_stats}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[watch] live_wp skipped: {exc}")

            # 有比赛进行中时切到更短的间隔：15 分钟一个点太少，一场比赛只有
            # 6~8 个样本，做不了校准。赛中改为 LIVE_INTERVAL（默认 120s）。
            try:
                live_now = has_live_games(conn, slate)
            except Exception:  # noqa: BLE001
                live_now = False

            # 滚球盘口快照（研究模块）：原样保存 market JSON，先不解释口径。
            if with_ingest:
                try:
                    from ..ingest import hao as hao_live

                    lo_stats = hao_live.store_live_odds(conn, slate)
                    if lo_stats.get("live_nba"):
                        print(f"[watch] live_odds: {lo_stats}")
                except Exception as exc:  # noqa: BLE001
                    print(f"[watch] live_odds skipped: {exc}")

            # 本机快速通道（可选，NBAQ_LOCAL_PUBLISH_LIVE=1）：
            # 只把「滚球盘口」小文件推到坚果云，绝不碰台账 / static.json。
            # 手机 APK 拉到后按 game_id 覆盖盘口，预测量仍用 github.io 的快照。
            if with_ingest and live_now and LOCAL_PUBLISH_LIVE:
                try:
                    from . import webdav
                    from .live import live_overlay

                    overlay = live_overlay(conn, slate)
                    if overlay["markets"] and webdav.enabled():
                        import json as _json

                        if webdav.put_text(
                            webdav.LIVE, _json.dumps(overlay, ensure_ascii=False)
                        ):
                            print(f"[watch] live overlay -> webdav: {overlay['count']} game(s)")
                except Exception as exc:  # noqa: BLE001
                    print(f"[watch] live overlay skipped: {exc}")

            # 赛时主动戳一轮 CI（workflow_dispatch 不受 schedule 限流）：
            # 让 github.io / 坚果云上的 static.json 保持约 10 分钟级新鲜。
            if with_ingest and live_now and CI_TRIGGER_MINUTES > 0:
                if time.time() - last_ci_trigger >= CI_TRIGGER_MINUTES * 60:
                    last_ci_trigger = time.time()
                    try:
                        from scripts.trigger_refresh import trigger_workflow

                        result = trigger_workflow()
                        print(f"[watch] ci trigger: {result['message']}")
                    except Exception as exc:  # noqa: BLE001
                        print(f"[watch] ci trigger failed: {exc}")

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

            # 台账 ← 坚果云（配置了 WebDAV 才跑）。本机只拉不推：CI 是权威台账
            # （它刚结算完并推送过），本机回推会拿较少的注单集覆盖云端。
            # 本机要把注单上云，走 CLI：python -m app.cli bets-sync --force。
            try:
                from . import betssync

                sync_stats = betssync.run(conn, pull_only=True)
                if (
                    sync_stats.get("inbox_merged")
                    or sync_stats.get("inbox_updated")
                    or sync_stats.get("ledger_merged")
                    or sync_stats.get("ledger_updated")
                    or sync_stats.get("pushed")
                    or sync_stats.get("static_pushed")
                ):
                    print(f"[watch] betssync: {sync_stats}")
            except Exception as exc:  # noqa: BLE001
                print(f"[watch] betssync failed: {exc}")

            try:
                predict_date(slate)
                # 只推通过可下注阀门（bettable）的信号：signal 只代表模型算出
                # 正 EV，bettable 才代表价格真实可拿、共识够深。
                all_items = notify.collect_signal_items(conn, slate, bettable_only=False)
                items = [(game, edge) for game, edge in all_items if edge.get("bettable")]
                if len(items) != len(all_items):
                    print(
                        f"[watch] notify: {len(all_items)} 个信号，"
                        f"{len(items)} 个通过可下注阀门（其余仅观察，不推送）"
                    )
                sent = notify.send_new_signals(conn, items)
                if sent:
                    print(f"[watch] pushed {sent} bettable signal(s)")
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
        # 赛中加速采样；比赛结束后回到常规间隔
        sleep_for = LIVE_INTERVAL if (with_ingest and live_now) else interval
        if live_now != was_live:
            print(
                f"[watch] {'live detected → ' if live_now else 'no live games → '}interval {sleep_for:.0f}s"
            )
            was_live = live_now
        time.sleep(sleep_for)
