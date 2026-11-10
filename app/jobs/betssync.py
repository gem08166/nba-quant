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
from datetime import datetime, timedelta, timezone

from .. import db
from ..config import DATA_DIR, ROOT
from . import webdav

STATE_PATH = DATA_DIR / "webdav_state.json"


def _ref(row: dict) -> str:
    """注单的稳定身份。

    优先级：显式 ref → 数字 id（服务端）→ natural key 兜底。
    数字 id 统一成 `srv-<id>`，与 `bets_payload()` 合成出来的 ref 一致，
    这样「原始 bets 表导出」和「台账 JSON」会落到同一个身份上。
    """
    ref = row.get("ref")
    if ref:
        return str(ref).strip()
    bid = row.get("id")
    if bid is not None and str(bid).strip():
        s = str(bid).strip()
        return f"srv-{s}" if s.isdigit() else s
    raw = f"{row.get('game_id')}|{row.get('market')}|{row.get('side')}|{row.get('line')}|{row.get('placed_at')}"
    return "gen-" + hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def _num(value) -> float | None:
    try:
        return round(float(value), 3)
    except (TypeError, ValueError):
        return None


def _norm_time(value) -> str:
    """把 placed_at 归一化到 UTC 秒，避免毫秒/时区写法不同导致重复导入。"""
    if not value:
        return ""
    text = str(value).strip()
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return text
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def natural_key(row: dict) -> tuple:
    """同一注单的自然键：跨设备/跨 ref 也能认出来。

    line 四舍五入到 3 位（0.0 / -0.0 / 1e-9 之类的浮点噪声），
    placed_at 归一到 UTC 秒（手机是 .000Z，服务端是 +00:00）。
    """
    return (
        str(row.get("game_id") or ""),
        str(row.get("market") or "").lower(),
        str(row.get("side") or "").lower(),
        _num(row.get("line")),
        _norm_time(row.get("placed_at")),
    )


def _find_existing(conn: sqlite3.Connection, row: dict, ref: str, allow_similar: bool = True):
    """先按 ref，再按自然键（同场/同市场/同方向/同线/同时间）。"""
    hit = conn.execute("SELECT * FROM bets WHERE ref=?", (ref,)).fetchone()
    if hit:
        return hit
    key = natural_key(row)
    if not key[0] or not key[1] or not key[2]:
        return None
    for cand in conn.execute(
        """
        SELECT * FROM bets
        WHERE game_id=? AND lower(market)=lower(?) AND lower(side)=lower(?)
        """,
        (key[0], key[1], key[2]),
    ):
        if natural_key(dict(cand)) == key:
            return cand
    return _find_similar_real(conn, row) if allow_similar else None


def _parse_ts(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _is_sim(bet: dict) -> bool:
    note = bet.get("note") or ""
    return "回填" in note or "模拟" in note


def _find_similar_real(conn: sqlite3.Connection, row: dict):
    """真实注单的「同一笔」兜底：同盘口 + 赔率差 ≤0.02 + 下注时间差 ≤12h。

    手机和 PC 可能各记一次（ref 不同、时间差几分钟），按精确时间匹配不到，
    台账就会出现两条重复。模拟/回填注单不参与这个匹配。
    """
    if _is_sim(row):
        return None
    when = _parse_ts(row.get("placed_at"))
    price = _num(row.get("price"))
    if when is None or price is None:
        return None
    for cand in conn.execute(
        """
        SELECT * FROM bets
        WHERE game_id=? AND lower(market)=lower(?) AND lower(side)=lower(?)
          AND line IS ?
        """,
        (
            str(row.get("game_id")),
            str(row.get("market") or "").lower(),
            str(row.get("side") or "").lower(),
            row.get("line"),
        ),
    ):
        current = dict(cand)
        if _is_sim(current):
            continue
        other_when = _parse_ts(current.get("placed_at"))
        other_price = _num(current.get("price"))
        if other_when is None or other_price is None:
            continue
        if abs(other_price - price) <= 0.02 + 1e-9 and abs(other_when - when) <= timedelta(hours=12):
            return cand
    return None



def bets_payload(conn: sqlite3.Connection) -> list[dict]:
    """全量台账（服务端视角），字段与 PWA 的注单对象对齐。"""
    rows = conn.execute(
        """
        SELECT b.*, g.slate_date, aa.abbrev AS away_abbrev, ha.abbrev AS home_abbrev
        FROM bets b
        LEFT JOIN games g ON g.id = b.game_id
        LEFT JOIN teams aa ON aa.id = g.away_id
        LEFT JOIN teams ha ON ha.id = g.home_id
        ORDER BY b.id
        """
    ).fetchall()
    payload = []
    for row in rows:
        item = dict(row)
        item["ref"] = row["ref"] or f"srv-{row['id']}"
        if row["away_abbrev"] and row["home_abbrev"]:
            item["matchup"] = f"{row['away_abbrev']}@{row['home_abbrev']}"
        else:
            # 服务端还没 ingest 这场比赛：保留注单，别让导出把它吞掉
            item["matchup"] = str(row["game_id"])
        payload.append(item)
    return payload


def normalize_refs(conn: sqlite3.Connection) -> int:
    """给没有 ref 的历史注单补上 `srv-<id>`（服务端创建的都走这个规则），
    否则云端合回时会因为 ref 对不上而产生重复。

    注意：bets.id 是普通 INTEGER PRIMARY KEY，删除后 rowid 会被复用，
    历史 `srv-<id>` 引用可能已经指向另一条注单（本地实测出现过
    `srv-53` 属于 id=37 的情况）。第一遍正常补；冲突的行退到
    `srv-<id>-x`（id 唯一，后缀保证不撞），绝不因为 UNIQUE 让整轮同步崩溃。
    """
    cur = conn.execute(
        """
        UPDATE bets SET ref = 'srv-' || id
        WHERE ref IS NULL
          AND NOT EXISTS (SELECT 1 FROM bets b2 WHERE b2.ref = 'srv-' || bets.id)
        """
    )
    first = cur.rowcount
    cur2 = conn.execute(
        """
        UPDATE bets SET ref = 'srv-' || id || '-x'
        WHERE ref IS NULL
          AND NOT EXISTS (SELECT 1 FROM bets b2 WHERE b2.ref = 'srv-' || bets.id || '-x')
        """
    )
    conn.commit()
    return first + cur2.rowcount


def merge_rows(
    conn: sqlite3.Connection,
    rows: list[dict],
    tombstones: set[str] | None = None,
) -> dict:
    """把 PWA 形状的注单合并进 bets 表（幂等：同一注单只落一次）。

    身份判定：
      1. `ref` / `id`（同一份导出重复导入 → 直接命中）；
      2. 自然键 `natural_key()`（换了 ref、毫秒/时区写法不同、浮点线噪声）。
    tombstones：已删除的 ref 集合——命中则直接跳过（旧版手机重新推送也复活不了）。
    合并规则（单调）：
      - 已存在的 open 注单 ← 云端已结算（won/lost/push）可覆盖，并补收盘线/CLV；
      - 双方都已结算且结果相同 → 只补空的收盘线/CLV/profit；
      - 已结算 ← open（任何情况都不降级）；
      - 其余重复 → 跳过。
    """
    added = updated = duplicates = invalid = blocked = 0
    tombstones = tombstones or set()
    seen: set[tuple] = set()
    for row in rows or []:
        if not isinstance(row, dict) or not row.get("game_id") or not row.get("market") or not row.get("side"):
            invalid += 1
            continue
        try:
            ref = _ref(row)
        except Exception:  # noqa: BLE001 - 单条坏数据不影响整批
            invalid += 1
            continue
        if ref in tombstones:
            blocked += 1  # 已删除的注单：旧版手机重新推送也不复活
            continue
        key = natural_key(row)
        if key in seen:  # 同一批文件里的重复行
            duplicates += 1
            continue
        seen.add(key)

        existing = _find_existing(
            conn, row, ref, allow_similar=not str(ref).startswith("hao-")
        )
        if existing is not None:
            current = dict(existing)
            incoming = row.get("result")
            cur_result = current.get("result") or "open"
            if incoming in ("won", "lost", "push"):
                if cur_result == "open" or (
                    str(ref).startswith("hao-")
                    and (cur_result != incoming or row.get("profit") != current.get("profit"))
                ):
                    # 333 平台订单是权威：平台结果可以覆盖本地自行结算的结果
                    cur = conn.execute(
                        """
                        UPDATE bets SET result=?, profit=?,
                            closing_line=COALESCE(closing_line, ?),
                            closing_price=COALESCE(closing_price, ?),
                            clv=COALESCE(clv, ?),
                            clv_line=COALESCE(clv_line, ?)
                        WHERE id=?
                        """,
                        (
                            incoming,
                            row.get("profit"),
                            row.get("closing_line"),
                            row.get("closing_price"),
                            row.get("clv"),
                            row.get("clv_line"),
                            current["id"],
                        ),
                    )
                    if cur.rowcount:
                        updated += 1
                    else:
                        duplicates += 1
                elif cur_result == incoming:
                    # 两边都已结算：只补空缺的收盘信息，不改结果
                    fills = [
                        (name, row.get(name))
                        for name in ("closing_line", "closing_price", "clv", "clv_line", "profit")
                        if row.get(name) is not None and current.get(name) is None
                    ]
                    if fills:
                        assignments = ", ".join(f"{name}=?" for name, _ in fills)
                        conn.execute(
                            f"UPDATE bets SET {assignments} WHERE id=?",
                            [value for _, value in fills] + [current["id"]],
                        )
                        updated += 1
                    else:
                        duplicates += 1
                else:
                    duplicates += 1
            else:
                duplicates += 1
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
                row.get("result") or "open",
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
    return {
        "added": added,
        "updated": updated,
        "skipped": duplicates + invalid + blocked,
        "duplicates": duplicates,
        "invalid": invalid,
        "blocked": blocked,
    }


def import_rows(conn: sqlite3.Connection, payload, tombstones: set[str] | None = None) -> dict:
    """接受多种导出形状：裸数组 / {bets:[...]} / {rows:[...]}。"""
    rows = payload
    if isinstance(payload, dict):
        rows = payload.get("bets")
        if rows is None:
            rows = payload.get("rows")
    if not isinstance(rows, list):
        return {"added": 0, "updated": 0, "skipped": 1, "duplicates": 0, "invalid": 1, "blocked": 0}
    return merge_rows(conn, rows, tombstones=tombstones)


def export_payload(conn: sqlite3.Connection) -> dict:
    """可再导入的台账（带 schema 版本，手机/PC 共用）。"""
    bets = bets_payload(conn)
    return {
        "schema": "nbaq.bets/1",
        "exported_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "count": len(bets),
        "bets": bets,
    }


def _state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


# ---------- 删除墓碑：跨设备传播「这条注单被删了」 ----------
# 没有墓碑时：本机清空 → 云端台账仍有 → 下一次同步又合并回来（实测发生过）。
# 有墓碑时：任何设备（本机 / CI）同步时都会先删掉墓碑里的 ref，再合并台账。

def load_deletions() -> set[str]:
    try:
        raw = webdav.get_text(webdav.DELETED)
    except Exception:  # noqa: BLE001 - 墓碑读不到不影响同步
        return set()
    if not raw:
        return set()
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return set()
    refs = payload.get("refs") if isinstance(payload, dict) else payload
    return {str(r) for r in (refs or []) if r}


def record_deletions(refs: list[str]) -> int:
    """把被删注单的 ref 追加到云端墓碑（幂等、去重、上限 1000 条）。"""
    wanted = {str(r) for r in refs if r}
    if not wanted:
        return 0
    merged = sorted(load_deletions() | wanted)[-1000:]
    body = json.dumps(
        {
            "schema": "nbaq.bets.deleted/1",
            "updated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "refs": merged,
        },
        ensure_ascii=False,
        indent=1,
    )
    return len(merged) if webdav.put_text(webdav.DELETED, body) else 0


def apply_deletions(conn: sqlite3.Connection, refs: set[str]) -> int:
    """删除墓碑里的注单（墓碑是「用户主动删除」的事实，优先于台账合并）。"""
    if not refs:
        return 0
    placeholders = ",".join("?" for _ in refs)
    rows = conn.execute(
        f"SELECT id FROM bets WHERE ref IN ({placeholders})",
        list(refs),
    ).fetchall()
    if not rows:
        return 0
    conn.execute(f"DELETE FROM bets WHERE ref IN ({placeholders})", list(refs))
    conn.commit()
    return len(rows)


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
        "deleted": 0,
    }

    # 先给历史注单补 ref（服务端创建的 = srv-<id>），否则云端合回会重复
    stats["refs_normalized"] = normalize_refs(conn)
    deletions = load_deletions()
    # 墓碑先删再合并：否则「已删除的手动注单」会和平台订单同自然键，
    # 导致新订单被当成重复跳过（实测 333 导入丢过 1 条）。
    stats["deleted"] = apply_deletions(conn, deletions)

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
                result = merge_rows(conn, rows if isinstance(rows, list) else [], tombstones=deletions)
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
                result = merge_rows(conn, rows, tombstones=deletions)
                stats["ledger_merged"] = result["added"]
                stats["ledger_updated"] = result.get("updated", 0)
        except (json.JSONDecodeError, TypeError) as exc:
            stats["ledger_error"] = str(exc)[:120]

    # 2.5) 合并后再清一次兜底（inbox / 台账里可能带回墓碑 ref）：
    #      先合并再删，保证手机重新导出的已删注单也不会复活。
    stats["deleted"] += apply_deletions(conn, deletions)

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
