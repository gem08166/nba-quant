# -*- coding: utf-8 -*-
"""夜间哨兵（VPS 专用）：只跑干净源，采集 + 预测 + 推送 + 导出。

为什么只跑干净源：333 / hao-tiyu 是博彩域名，国内云 VPS 访问会触发服务商
合规审计（TLS SNI 明文可见），可能被整改/隔离/终止服务。ESPN 与 The Odds API
是公开体育数据与商业赔率 API，无合规风险；两者合计 10 家书源，足够过共识闸门。

分工：
    VPS（整夜）：本脚本循环 —— ESPN 伤停/盘口 + Odds API 9 家 → predict → ntfy 推送
                 → 导出 nbaq/sentinel/<slate>.json（坚果云）
    本机（白天）：开机后 watch 启动时自动 pull_recent() 合并，再补抓 333/hao 实时价

用法：
    python -m scripts.sentinel            # 常驻循环（systemd 用）
    python -m scripts.sentinel --once     # 跑一轮后退出（部署自检）
    python -m scripts.sentinel --once --export-file /tmp/sentinel.json  # 不传 WebDAV
"""
from __future__ import annotations

import argparse
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import db  # noqa: E402
from app.config import SENTINEL_CLEAN, WATCH_INTERVAL  # noqa: E402
from app.dates import current_slate_date  # noqa: E402
from app.ingest import service, theodds  # noqa: E402
from app.jobs import notify, sentinel  # noqa: E402
from app.jobs.predict import predict_date  # noqa: E402

LOG = ROOT / "data" / "sentinel.log"
# 导出节流：每小时传一次（8 小时夜间 ≈ 8 次 × ~450KB，坚果云流量可控）
EXPORT_INTERVAL = 3600.0


def _log(msg: str) -> None:
    line = f"{datetime.now(timezone.utc).astimezone().strftime('%m-%d %H:%M:%S')} {msg}"
    try:
        with LOG.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    print(line, flush=True)


def run_once(export_file: str | None = None, do_export: bool = True) -> dict:
    slate = current_slate_date()
    conn = db.connect()
    out: dict = {"slate": slate}
    try:
        # 1) ESPN：比分/状态/盘口/伤停（干净源）
        try:
            out["ingest"] = service.ingest_date(slate)
        except Exception as exc:  # noqa: BLE001
            out["ingest_error"] = str(exc)

        # 2) The Odds API：9 家书源（干净源，自带 2h 闸门）
        try:
            msg = theodds.store_odds_api(conn, slate_date=slate)
            conn.commit()
            if msg:
                out["odds_api"] = msg
        except Exception as exc:  # noqa: BLE001
            out["odds_api_error"] = str(exc)

        # 3) 台账只读拉取：让推送去重知道「这个市场已经有在台单」
        try:
            from app.jobs import betssync

            out["betsync"] = betssync.run(conn, pull_only=True)
        except Exception as exc:  # noqa: BLE001
            out["betsync_error"] = str(exc)

        # 4) 预测 + 闸门 + 推送（与 watch 同一套逻辑）
        try:
            predict_date(slate)
            all_items = notify.collect_signal_items(conn, slate, bettable_only=False)
            items = [(g, e) for g, e in all_items if e.get("bettable")]
            out["signals_total"] = len(all_items)
            out["signals_bettable"] = len(items)
            out["pushed"] = notify.send_new_signals(conn, items)
        except Exception as exc:  # noqa: BLE001
            out["predict_error"] = str(exc)

        # 5) 导出到 WebDAV（本机开机时拉回）
        if do_export:
            try:
                out["export"] = sentinel.export_day(conn, slate, dry_run_path=export_file)
            except Exception as exc:  # noqa: BLE001
                out["export_error"] = str(exc)
        return out
    finally:
        conn.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="夜间哨兵：干净源采集+预测+推送")
    parser.add_argument("--once", action="store_true", help="跑一轮后退出")
    parser.add_argument("--interval", type=float, default=None, help="循环间隔（秒）")
    parser.add_argument("--export-file", default=None, help="导出到本地文件（不传 WebDAV）")
    args = parser.parse_args()

    if not SENTINEL_CLEAN:
        _log("警告：NBAQ_SENTINEL_CLEAN 未开启（本脚本本身已不含博彩源，仅提示）")

    interval = args.interval or WATCH_INTERVAL
    _log(f"sentinel start interval={interval:.0f}s once={args.once}")
    last_export = 0.0
    while True:
        try:
            # 导出每小时一次：每轮都传 450KB 会让坚果云流量白白翻四倍
            now = time.time()
            do_export = args.once or (now - last_export >= EXPORT_INTERVAL)
            stats = run_once(export_file=args.export_file, do_export=do_export)
            if stats.get("export"):
                last_export = now
            _log(
                "round "
                + " ".join(
                    f"{k}={v}"
                    for k, v in stats.items()
                    if k in ("slate", "signals_bettable", "pushed")
                )
                + (f" export={stats['export']}" if stats.get("export") else "")
                + (f" ERR={stats['ingest_error']}" if stats.get("ingest_error") else "")
            )
        except Exception:  # noqa: BLE001
            _log("round crashed:\n" + traceback.format_exc())
        if args.once:
            return 0
        time.sleep(interval)


if __name__ == "__main__":
    sys.exit(main())
