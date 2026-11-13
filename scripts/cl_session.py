"""比赛日 CL 捕捉会话。

在比赛日自动执行完整的收盘线捕捉流程：
1. 查询当日赛程，计算捕捉窗口（首场开赛前 2 小时 → 最后一场结束后 30 分钟）
2. 等待至窗口开启
3. 窗口期内高频捕捉（ESPN 盘口 + 好体育滚球 + The Odds API 收盘豁免）
4. 最后一场 STATUS_FINAL 后做最终收盘捕捉，然后退出

用法：
    python -m scripts.cl_session              # 完整会话（等待 + 捕捉 + 退出）
    python -m scripts.cl_session --now        # 跳过等待，立即开始捕捉
    python -m scripts.cl_session --interval 180  # 自定义捕捉间隔（秒）
    python -m scripts.cl_session --dry-run    # 只打印计划，不实际执行

部署：Windows 计划任务每天 06:00（北京时间）触发一次，脚本自行管理等待和退出。
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import DATA_DIR
from app.dates import current_slate_date
from app.jobs.settle import capture_closing, settle

BEIJING = timezone(timedelta(hours=8))


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _bj(dt: datetime) -> str:
    return dt.astimezone(BEIJING).strftime("%m-%d %H:%M")


def _log(msg: str) -> None:
    ts = datetime.now(BEIJING).strftime("%H:%M:%S")
    print(f"[CL {ts}] {msg}", flush=True)


def _parse_ts(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _slate_games(conn: sqlite3.Connection, slate_date: str) -> list[dict]:
    rows = conn.execute(
        """
        SELECT g.id, g.tipoff, g.status,
               h.abbrev AS home, a.abbrev AS away
        FROM games g
        JOIN teams h ON h.id = g.home_id
        JOIN teams a ON a.id = g.away_id
        WHERE g.slate_date = ?
          AND g.status NOT IN ('STATUS_POSTPONED', 'STATUS_CANCELED', 'STATUS_CANCELLED')
        ORDER BY g.tipoff
        """,
        (slate_date,),
    ).fetchall()
    return [dict(r) for r in rows]


def _slate_window(games: list[dict]) -> tuple[datetime | None, datetime | None]:
    """返回 (最早开赛, 最晚开赛)。"""
    tips = [_parse_ts(g["tipoff"]) for g in games]
    tips = [t for t in tips if t]
    if not tips:
        return None, None
    return min(tips), max(tips)


def _all_final(conn: sqlite3.Connection, games: list[dict]) -> bool:
    """所有比赛都 STATUS_FINAL 且有比分。"""
    for g in games:
        row = conn.execute(
            "SELECT status, home_score, away_score FROM games WHERE id = ?", (g["id"],)
        ).fetchone()
        if not row:
            return False
        if row["status"] != "STATUS_FINAL" or row["home_score"] is None:
            return False
    return True


def _any_live(conn: sqlite3.Connection, games: list[dict]) -> bool:
    for g in games:
        row = conn.execute(
            "SELECT status FROM games WHERE id = ?", (g["id"],)
        ).fetchone()
        if row and row["status"] == "STATUS_IN_PROGRESS":
            return True
    return False


def run_session(interval: int, skip_wait: bool, dry_run: bool) -> None:
    conn = sqlite3.connect(str(DATA_DIR / "nba.db"))
    conn.row_factory = sqlite3.Row

    # 收盘捕捉去重表（内存级，每次会话重建）
    conn.execute(
        "CREATE TABLE IF NOT EXISTS cl_captured ("
        "game_id TEXT PRIMARY KEY, captured_at TEXT NOT NULL)"
    )

    slate = current_slate_date()
    games = _slate_games(conn, slate)

    if not games:
        _log(f"赛程 {slate} 无比赛，退出")
        return

    first_tip, last_tip = _slate_window(games)
    if not first_tip:
        _log("无法解析任何开赛时间，退出")
        return

    # 捕捉窗口：首场前 2h → 最后一场后 30min
    window_start = first_tip - timedelta(hours=2)
    window_end_soft = last_tip + timedelta(minutes=30)

    _log(f"赛程 {slate}：{len(games)} 场")
    _log(f"  首场 {_bj(first_tip)} | 末场 {_bj(last_tip)}（北京时间）")
    _log(f"  捕捉窗口 {_bj(window_start)} → {_bj(window_end_soft)}")

    if dry_run:
        _log("dry-run 模式，退出")
        return

    # ===== 等待阶段 =====
    now = _now()
    if not skip_wait and now < window_start:
        wait_secs = (window_start - now).total_seconds()
        _log(f"等待 {wait_secs / 60:.0f} 分钟至窗口开启 {_bj(window_start)}")
        # 分段睡眠（每 5 分钟醒来看一眼是否有延迟/取消）
        while True:
            now = _now()
            if now >= window_start:
                break
            remaining = (window_start - now).total_seconds()
            chunk = min(remaining, 300)
            time.sleep(chunk)
            # 检查比赛状态是否有变化（延迟/取消）
            games = _slate_games(conn, slate)
            if not games:
                _log("所有比赛被取消/延期，退出")
                return
            first_tip, _ = _slate_window(games)
            if first_tip and _now() >= first_tip - timedelta(hours=2):
                break
        _log("窗口开启，开始捕捉")

    # ===== 捕捉阶段 =====
    round_num = 0
    final_captured = False

    while True:
        round_num += 1
        now = _now()

        # 刷新比赛状态
        games = _slate_games(conn, slate)
        if not games:
            _log("所有比赛结束或取消，退出捕捉循环")
            break

        finals = sum(1 for g in games if g["status"] == "STATUS_FINAL")
        live = sum(1 for g in games if g["status"] == "STATUS_IN_PROGRESS")
        pregame = len(games) - finals - live
        _log(f"第 {round_num} 轮 | 已完赛 {finals} | 进行中 {live} | 未开赛 {pregame}")

        # a) ESPN 盘口收盘捕捉（含 The Odds API 收盘豁免）
        try:
            from app.jobs.settle import capture_closing

            captured = capture_closing(conn)
            _log(f"  ESPN 盘口: {len(captured)} 场")
        except Exception as exc:
            _log(f"  ESPN 盘口失败: {exc}")

        # b) The Odds API：每场只做一次收盘捕捉（开赛前 30 分钟触发）
        #    高频轮次用 ESPN + 好体育（免费），Odds API 仅做参考基准
        try:
            from app.ingest import theodds

            now_iso = _now().isoformat(timespec="seconds")
            pending = []
            for g in games:
                tip = _parse_ts(g["tipoff"])
                if not tip:
                    continue
                mins_to_tip = (tip - _now()).total_seconds() / 60.0
                # 收盘时刻：开赛前 25~35 分钟窗口内
                if 25 <= mins_to_tip <= 35:
                    # 检查是否已捕捉过
                    done = conn.execute(
                        "SELECT 1 FROM cl_captured WHERE game_id = ?",
                        (g["id"],),
                    ).fetchone()
                    if not done:
                        pending.append(g)

            if pending:
                events, msg = theodds.fetch_nba_odds()
                if events:
                    from app.ingest.theodds import ingest_events

                    n = theodds.ingest_events(conn, events, slate_date=slate)
                    for g in pending:
                        conn.execute(
                            "INSERT OR IGNORE INTO cl_captured(game_id, captured_at) VALUES (?, ?)",
                            (g["id"], now_iso),
                        )
                    _log(f"  Odds API 收盘: {msg} · {len(pending)} 场 · 入库 {n}")
                else:
                    _log(f"  Odds API: {msg}")
            else:
                _log("  Odds API: 无待捕捉比赛（收盘时刻未到或已完成）")
        except Exception as exc:
            _log(f"  Odds API 失败: {exc}")

        # c) 好体育滚球盘
        try:
            from app.ingest import hao

            lo_stats = hao.store_live_odds(conn, slate)
            if lo_stats.get("live_nba"):
                _log(f"  滚球盘: {lo_stats}")
        except Exception as exc:
            _log(f"  滚球盘失败: {exc}")

        # d) ESPN 实时比分（补状态 + 比分）
        try:
            from app.ingest import service

            day_stats = service.ingest_date(slate)
            _log(f"  ESPN ingest: {day_stats}")
        except Exception as exc:
            _log(f"  ESPN ingest 失败: {exc}")

        # e) 结算已完赛注单
        try:
            settled = settle(conn)
            if settled:
                for item in settled:
                    _log(f"  结算 #{item['bet_id']}: {item['result']} 盈亏 {item['profit']:+.2f}")
        except Exception as exc:
            _log(f"  结算失败: {exc}")

        conn.commit()

        # f) 检查是否全部结束
        if _all_final(conn, games):
            if not final_captured:
                _log("全部比赛已完赛，做最终收盘捕捉")
                final_captured = True
                # 最终一轮：收盘线 + 滚球终值
                try:
                    from app.jobs.settle import capture_closing

                    capture_closing(conn)
                except Exception:
                    pass
                # 结算所有注单
                try:
                    settled = settle(conn)
                    for item in settled:
                        _log(f"  最终结算 #{item['bet_id']}: {item['result']} 盈亏 {item['profit']:+.2f}")
                except Exception:
                    pass
                conn.commit()
            _log("全部完赛 + 最终收盘完成，休眠退出")
            break

        # g) 窗口后安全阀：超过最后一场 + 30 分钟仍未全部完赛 → 再等一轮后退出
        if now > window_end_soft + timedelta(minutes=60):
            _log("超过窗口安全线 60 分钟，强制退出")
            break

        # h) 等待下一轮
        time.sleep(interval)

    # ===== 收尾 =====
    _log("CL 捕捉会话结束")


def main() -> None:
    parser = argparse.ArgumentParser(description="比赛日 CL 捕捉会话")
    parser.add_argument("--interval", type=int, default=180, help="捕捉间隔（秒），默认 180")
    parser.add_argument("--now", action="store_true", help="跳过等待，立即开始捕捉")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不执行")
    args = parser.parse_args()

    run_session(interval=args.interval, skip_wait=args.now, dry_run=args.dry_run)


if __name__ == "__main__":
    main()
