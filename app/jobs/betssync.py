"""台账 ↔ 坚果云 同步（方案：PC 端服务端直连 WebDAV，手机 PWA 通过文件中转）。

数据流：
    1. 拉取 nbaq/inbox/*.json（手机「导出台账」后经坚果云 app 上传的片段），
       逐条合并进本地 bets 表（按 ref 去重），合并完 MOVE 到 nbaq/inbox/done/。
    2. 拉取 nbaq/bets.json（云端全量台账，可能来自另一台 PC），合并进本地。
    3. 把本地全量台账（含结算结果）推送回 nbaq/bets.json（内容没变就跳过）。

合并键 ref：静态端 = 注单字符串 id（"local-..."）；服务端 = "srv-<数字id>"。
云端/手机来的注单若无 ref，用 placed_at+game+market+side 兜底生成。
"""
from __future__ import annotations

import hashlib
import json
import sqlite3

from .. import db
from ..config import DATA_DIR, ROOT
from . import webdav

STATE_PATH = DATA_DIR / "webdav_state.json"


def _ref(row: dict) -> str:
    ref = row.get("ref") or row.get("id")
    if ref:
        return str(ref)
    raw = f"{row.get('game_id')}|{row.get('market')}|{row.get('side')}|{row.get('line')}|{row.get('placed_at')}"
    return "gen-" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def bets_payload(conn: sqlite3.Connection) -> list[dict]:
    """全量台账（服务端视角），字段与 PWA 的注单对象对齐。"""
    rows = conn.execute(
        """
        SELECT b.*, g.slate_date, aa.abbrev AS away_abbrev, ha.abbrev AS home_abbrev
        FROM bets b
        JOIN games g ON g.id = b.game_id
        JOIN teams aa ON aa.id = g.away_id
        JOIN teams ha ON ha.id = g.home_id
        ORDER BY b.id
        """
    ).fetchall()
    payload = []
    for row in rows:
        item = dict(row)
        item["ref"] = row["ref"] or f"srv-{row['id']}"
        item["matchup"] = f"{row['away_abbrev']}@{row['home_abbrev']}"
        payload.append(item)
    return payload


def normalize_refs(conn: sqlite3.Connection) -> int:
    """给没有 ref 的历史注单补上 `srv-<id>`（服务端创建的都走这个规则），
    否则云端合回时会因为 ref 对不上而产生重复。"""
    cur = conn.execute("UPDATE bets SET ref = 'srv-' || id WHERE ref IS NULL")
    conn.commit()
    return cur.rowcount


def merge_rows(conn: sqlite3.Connection, rows: list[dict]) -> dict:
    """把 PWA 形状的注单合并进 bets 表（按 ref 去重，自然键二次兜底）。

    对已存在的注单做**单调更新**：云端已结算（won/lost/push）的结果可以覆盖
    本地的 open，并补上收盘线/CLV；反过来（已结算 → open）绝不覆盖。
    这样本机只读模式也能跟上 CI 的结算进度。
    """
    added = skipped = updated = 0
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("game_id") or not row.get("market") or not row.get("side"):
            skipped += 1
            continue
        try:
            ref = _ref(row)
        except Exception:  # noqa: BLE001 - 单条坏数据不影响整批
            skipped += 1
            continue
        exists = conn.execute("SELECT 1 FROM bets WHERE ref=?", (ref,)).fetchone()
        if not exists:
            # 自然键兜底：同场同市场同方向同线同时间的注单视为同一条
            # （防止 ref 规则不一致时产生重复）
            exists = conn.execute(
                """
                SELECT 1 FROM bets WHERE game_id=? AND market=? AND side=?
                  AND line IS ? AND COALESCE(placed_at,'')=COALESCE(?,'')
                """,
                (row.get("game_id"), row.get("market"), row.get("side"), row.get("line"), row.get("placed_at")),
            ).fetchone()
        if exists:
            # 单调更新：只允许「云端已结算」补齐本地 open 的注单
            incoming = row.get("result")
            if incoming in ("won", "lost", "push"):
                cur = conn.execute(
                    """
                    UPDATE bets SET result=?, profit=?,
                        closing_line=COALESCE(closing_line, ?),
                        closing_price=COALESCE(closing_price, ?),
                        clv=COALESCE(clv, ?),
                        clv_line=COALESCE(clv_line, ?)
                    WHERE ref=? AND (result IS NULL OR result='open')
                    """,
                    (
                        incoming,
                        row.get("profit"),
                        row.get("closing_line"),
                        row.get("closing_price"),
                        row.get("clv"),
                        row.get("clv_line"),
                        ref,
                    ),
                )
                updated += cur.rowcount
            skipped += 1
            continue
        conn.execute(
            """
            INSERT INTO bets(game_id, market, side, line, price, stake, model_prob, ev,
                             placed_at, result, profit, closing_line, closing_price, clv,
                             clv_line, note, ref)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                str(row.get("game_id") or ""),
                str(row.get("market") or ""),
                str(row.get("side") or ""),
                row.get("line"),
                row.get("price"),
                row.get("stake", 1.0),
                row.get("model_prob"),
                row.get("ev"),
                row.get("placed_at"),
                row.get("result", "open"),
                row.get("profit"),
                row.get("closing_line"),
                row.get("closing_price"),
                row.get("clv"),
                row.get("clv_line"),
                row.get("note"),
                ref,
            ),
        )
        added += 1
    conn.commit()
    return {"added": added, "skipped": skipped, "updated": updated}


def _state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def run(
    conn: sqlite3.Connection,
    force_push: bool = False,
    push_static: bool = False,
    slate_date: str | None = None,
    pull_only: bool = False,
) -> dict:
    """一轮同步：inbox → 本地；云端台账 → 本地；本地 → 云端。

    push_static：把 site/data/static.json 推到云端（手机 APK 的数据通道）。
    只在 CI（导出刚完成、数据最新）调用；PC 不推，避免旧数据覆盖新数据。
    slate_date：手机快照合并时的比赛日过滤（默认按 event 的 ET 日期自动匹配）。
    pull_only：只拉不推（PC 静默采样模式用）。CI 是权威台账（它刚结算过），
    本机拉下来看即可；回推会把本机较少的注单集盖掉云端——除非本地确实有
    云端没有的新注单，那走 CLI 的 bets-sync --force。
    """
    if not webdav.enabled():
        return {"skipped": "webdav not configured"}
    stats: dict = {
        "inbox_merged": 0,
        "inbox_updated": 0,
        "ledger_merged": 0,
        "ledger_updated": 0,
        "pushed": False,
    }

    # 先给历史注单补 ref（服务端创建的 = srv-<id>），否则云端合回会重复
    stats["refs_normalized"] = normalize_refs(conn)

    # 1) inbox：手机导出的片段
    #    pull_only：只读合并，绝不 MOVE（MOVE 会把文件从 inbox 拿走，CI 就永远
    #    看不到这条注单）。每轮重复读同一文件是幂等的（按 ref 去重）。
    if not pull_only:
        webdav.ensure_folders()
    try:
        for path in webdav.list_files(webdav.INBOX):
            if not path.endswith(".json"):
                continue
            try:
                text = webdav.get_text(path)
                rows = json.loads(text or "[]")
                if isinstance(rows, dict):
                    rows = rows.get("bets") or []
                result = merge_rows(conn, rows if isinstance(rows, list) else [])
                stats["inbox_merged"] += result["added"]
                stats["inbox_updated"] += result.get("updated", 0)
                if not pull_only:
                    webdav.move(path, f"{webdav.DONE}/{path.rsplit('/', 1)[-1]}")
            except Exception as exc:  # noqa: BLE001 - 单个文件坏了不影响其他
                stats[f"inbox_error:{path}"] = str(exc)[:120]
    except Exception as exc:  # noqa: BLE001 - 目录不可读不影响台账同步
        stats["inbox_error"] = str(exc)[:120]

    # 2) 云端全量台账 → 本地
    ledger_text = webdav.get_text(webdav.LEDGER)
    if ledger_text:
        try:
            rows = json.loads(ledger_text)
            if isinstance(rows, list):
                result = merge_rows(conn, rows)
                stats["ledger_merged"] = result["added"]
                stats["ledger_updated"] = result.get("updated", 0)
        except (json.JSONDecodeError, TypeError) as exc:
            stats["ledger_error"] = str(exc)[:120]

    # 3) 本地全量台账 → 云端（内容没变就跳过，省坚果云请求配额）
    if pull_only:
        return stats
    payload = bets_payload(conn)
    body = json.dumps(payload, ensure_ascii=False, indent=1)
    digest = hashlib.md5(body.encode("utf-8")).hexdigest()
    state = _state()
    if force_push or state.get("ledger_hash") != digest:
        if webdav.put_text(webdav.LEDGER, body):
            state["ledger_hash"] = digest
            _save_state(state)
            stats["pushed"] = True

    # 4) 手机快照合并：nbaq/snapshots/inbox/*.json（Odds API events 形状）
    for path in webdav.list_files(webdav.SNAP_INBOX):
        if not path.endswith(".json"):
            continue
        try:
            text = webdav.get_text(path)
            payload_snap = json.loads(text or "{}")
            events = payload_snap.get("events") or []
            from ..ingest import theodds

            stats["snapshot_rows"] = stats.get("snapshot_rows", 0) + theodds.ingest_events(
                conn, events, slate_date=slate_date
            )
            stats["snapshot_files"] = stats.get("snapshot_files", 0) + 1
            webdav.move(path, f"{webdav.SNAP_DONE}/{path.rsplit('/', 1)[-1]}")
        except Exception as exc:  # noqa: BLE001 - 单个文件坏了不影响其他
            stats[f"snapshot_error:{path}"] = str(exc)[:120]

    # 5) 静态站数据通道（仅 CI：--push-static；PC 不推，避免旧数据覆盖新数据）
    if push_static:
        static_path = ROOT / "site" / "data" / "static.json"
        if static_path.exists():
            body_static = static_path.read_text(encoding="utf-8")
            digest_s = hashlib.md5(body_static.encode("utf-8")).hexdigest()
            if state.get("static_hash") != digest_s:
                if webdav.put_text(webdav.STATIC, body_static):
                    state["static_hash"] = digest_s
                    _save_state(state)
                    stats["static_pushed"] = True
    return stats


def status() -> dict:
    conn = db.connect()
    try:
        return {
            "webdav": webdav.enabled(),
            "local_bets": conn.execute("SELECT COUNT(*) n FROM bets").fetchone()["n"],
            "state": _state(),
        }
    finally:
        conn.close()
