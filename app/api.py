from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from . import db, i18n
from .config import DATA_DIR, SIGNAL_MAX_AGE_HOURS
from .dates import current_slate_date
from .ingest.service import insert_odds
from .jobs import betssync, live
from .jobs.predict import latest_odds, latest_prediction, predict_date
from .jobs.settle import bet_kind, bet_source, summarize
from .models import rapm

router = APIRouter(prefix="/api")

CLOSED_GAME_STATUSES = {
    "STATUS_IN_PROGRESS",
    "STATUS_HALFTIME",
    "STATUS_FINAL",
    "STATUS_POSTPONED",
    "STATUS_CANCELED",
    "STATUS_CANCELLED",
}


def _parse_ts(value: str | None):
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _expire_edges(edges: list[dict], row: sqlite3.Row) -> list[dict]:
    """把过时信号标记为过期（signal/bettable=False），不改写 DB 历史。

    过时判定：
    - 比赛已开赛/中场/完赛/延期/取消（pregame 窗口已关闭）
    - 或信号生成后超过 SIGNAL_MAX_AGE_HOURS 未更新（盘口早已变化）
    """
    now = datetime.now(timezone.utc)
    tipoff = _parse_ts(row["tipoff"])
    closed = row["status"] in CLOSED_GAME_STATUSES or (tipoff is not None and tipoff <= now)
    for edge in edges:
        if not (edge.get("signal") or edge.get("bettable")):
            continue
        reason = None
        if closed:
            reason = "比赛已开始或结束"
        else:
            created = _parse_ts(edge.get("created_at"))
            if created and (now - created).total_seconds() > SIGNAL_MAX_AGE_HOURS * 3600:
                reason = f"信号超过 {SIGNAL_MAX_AGE_HOURS:g} 小时未更新"
        if reason:
            edge["signal"] = False
            edge["bettable"] = False
            edge["expired"] = True
            edge["expired_reason"] = reason
    return edges


def get_conn():
    conn = db.connect()
    try:
        yield conn
    finally:
        conn.close()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _game_payload(row: sqlite3.Row, prediction: dict | None) -> dict:
    home_abbrev, away_abbrev = row["home_abbrev"], row["away_abbrev"]
    home_name, away_name = row["home_name"], row["away_name"]
    payload = {
        "id": row["id"],
        "tipoff": row["tipoff"],
        "status": row["status"],
        "status_detail": row["status_detail"],
        "season": row["season"],
        "matchup": f"{away_abbrev} @ {home_abbrev}",
        "matchup_zh": i18n.matchup_zh(f"{away_abbrev} @ {home_abbrev}"),
        "home": {
            "id": row["home_id"],
            "abbrev": home_abbrev,
            "name": home_name,
            "name_zh": i18n.team_zh(home_abbrev, home_name),
            "short_zh": i18n.team_short_zh(home_abbrev, home_name),
        },
        "away": {
            "id": row["away_id"],
            "abbrev": away_abbrev,
            "name": away_name,
            "name_zh": i18n.team_zh(away_abbrev, away_name),
            "short_zh": i18n.team_short_zh(away_abbrev, away_name),
        },
        "home_score": row["home_score"],
        "away_score": row["away_score"],
        "live_home_score": row["live_home_score"],
        "live_away_score": row["live_away_score"],
        "prediction": None,
        "edges": [],
        "best_edge": None,
    }
    if prediction:
        details = prediction["details"]
        payload["prediction"] = {
            "pred_margin": prediction["pred_margin"],
            "sigma_margin": prediction["sigma_margin"],
            "pred_total": prediction["pred_total"],
            "sigma_total": prediction["sigma_total"],
            "home_win_prob": prediction["home_win_prob"],
            "book": details.get("book"),
            "lines": details.get("lines"),
            "probabilities": details.get("probabilities"),
            "pricing": details.get("pricing"),
            "consensus": details.get("consensus"),
            "divergence_risk": details.get("divergence_risk"),
            "created_at": prediction["created_at"],
        }
        payload["edges"] = _expire_edges(details.get("edges", []), row)
        payload["best_edge"] = payload["edges"][0] if payload["edges"] else None
    return payload


@router.get("/health")
def health(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    games = conn.execute("SELECT COUNT(*) AS n FROM games").fetchone()["n"]
    return {"ok": True, "games": games, "time": _now()}


@router.get("/games")
def games(
    date: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    slate = date or current_slate_date()
    rows = conn.execute(
        """
        SELECT g.*, h.abbrev AS home_abbrev, h.name AS home_name,
               a.abbrev AS away_abbrev, a.name AS away_name
        FROM games g
        JOIN teams h ON h.id = g.home_id
        JOIN teams a ON a.id = g.away_id
        WHERE g.slate_date = ?
        ORDER BY COALESCE(g.tipoff, '')
        """,
        (slate,),
    ).fetchall()
    items = []
    for row in rows:
        prediction = latest_prediction(conn, row["id"])
        items.append(_game_payload(row, prediction))
    signals = [
        edge
        for item in items
        for edge in item["edges"]
        if edge.get("signal")
    ]
    return {"date": slate, "count": len(items), "signals": len(signals), "games": items}


@router.get("/edges")
def edges(
    date: str | None = None,
    min_ev: float = 0.0,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    slate = date or current_slate_date()
    rows = conn.execute(
        """
        SELECT g.*, h.abbrev AS home_abbrev, a.abbrev AS away_abbrev
        FROM games g
        JOIN teams h ON h.id = g.home_id
        JOIN teams a ON a.id = g.away_id
        WHERE g.slate_date = ?
        ORDER BY COALESCE(g.tipoff, '')
        """,
        (slate,),
    ).fetchall()
    out = []
    for row in rows:
        prediction = latest_prediction(conn, row["id"])
        if not prediction:
            continue
        row_edges = _expire_edges(list(prediction["details"].get("edges", [])), row)
        for item in row_edges:
            if not item.get("signal") or item.get("ev", 0) < min_ev:
                continue
            out.append(
                {
                    "game_id": row["id"],
                    "tipoff": row["tipoff"],
                    "matchup": f"{row['away_abbrev']} @ {row['home_abbrev']}",
                    **item,
                }
            )
    out.sort(key=lambda item: item["ev"], reverse=True)
    return {"date": slate, "count": len(out), "edges": out}


@router.get("/games/{game_id}")
def game_detail(game_id: str, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    row = conn.execute(
        """
        SELECT g.*, h.abbrev AS home_abbrev, h.name AS home_name,
               a.abbrev AS away_abbrev, a.name AS away_name
        FROM games g
        JOIN teams h ON h.id = g.home_id
        JOIN teams a ON a.id = g.away_id
        WHERE g.id = ?
        """,
        (game_id,),
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="game not found")

    prediction = latest_prediction(conn, game_id)
    payload = _game_payload(row, prediction)
    odds = latest_odds(conn, game_id)
    odds.sort(key=lambda item: (item["market"], item["side"], item["book"], item["line"] or 0))
    injuries = [
        i18n.with_zh(dict(item))
        for item in conn.execute(
            "SELECT * FROM injuries WHERE game_id=? ORDER BY team_id, status, player",
            (game_id,),
        )
    ]
    suggestions = [
        i18n.with_zh(dict(item))
        for item in ((prediction or {}).get("details", {}).get("injury_suggestions", []) or [])
    ]
    return {"game": payload, "odds": odds, "injuries": injuries, "injury_suggestions": suggestions}


@router.get("/rapm")
def rapm_top(
    limit: int = 50,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    """Latest player RAPM ratings (net points per 100 possessions)."""
    model = rapm.latest_model(conn)
    if not model:
        raise HTTPException(status_code=404, detail="no RAPM model; run 'nbaq rapm-train'")
    ranked = sorted(model["ratings"].items(), key=lambda kv: kv[1], reverse=True)
    names = model.get("names") or {}
    players = [
        {
            "id": pid,
            "name": names.get(pid, pid),
            "name_zh": i18n.player_zh(names.get(pid, pid)),
            "rating": round(value, 2),
        }
        for pid, value in ranked[: max(1, min(limit, 800))]
    ]
    return {
        "created_at": model["created_at"],
        "n_games": model["n_games"],
        "n_stints": model["n_stints"],
        "players": players,
    }


class ManualOddsIn(BaseModel):
    game_id: str
    market: Literal["spread", "total", "moneyline"]
    side: str
    line: float | None = None
    price: float = Field(gt=1.0)
    book: str = "manual"


@router.post("/odds")
def add_odds(body: ManualOddsIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    game = conn.execute("SELECT id FROM games WHERE id=?", (body.game_id,)).fetchone()
    if not game:
        raise HTTPException(status_code=404, detail="game not found")
    valid_sides = {"home", "away"} if body.market != "total" else {"over", "under"}
    if body.side not in valid_sides:
        raise HTTPException(status_code=400, detail=f"side must be one of {sorted(valid_sides)}")
    if body.market != "moneyline" and body.line is None:
        raise HTTPException(status_code=400, detail="line is required for spread/total")
    inserted = insert_odds(
        conn,
        body.game_id,
        [
            {
                "book": body.book,
                "market": body.market,
                "side": body.side,
                "line": body.line,
                "price": body.price,
                "source": "manual",
            }
        ],
    )
    conn.commit()
    return {"inserted": inserted}


class PredictIn(BaseModel):
    date: str


@router.post("/predict")
def run_predict(body: PredictIn) -> dict:
    results = predict_date(body.date)
    return {"date": body.date, "count": len(results)}


class BetIn(BaseModel):
    game_id: str
    market: str
    side: str
    line: float | None = None
    price: float = Field(gt=1.0)
    stake: float = Field(gt=0)
    model_prob: float = Field(ge=0.0, le=1.0)
    ev: float
    note: str | None = None


class BetPatch(BaseModel):
    """手工修订注单：市场/方向/盘口/赔率/注额/备注；已结算的还能改结果与盈亏。"""

    market: Literal["spread", "total", "moneyline"] | None = None
    side: Literal["home", "away", "over", "under"] | None = None
    line: float | None = None
    price: float | None = Field(default=None, gt=1.0)
    stake: float | None = Field(default=None, gt=0)
    note: str | None = None
    result: Literal["open", "won", "lost", "push"] | None = None
    profit: float | None = None
    # 危险操作显式确认：删除已结算注单必须带 force=true
    force: bool = False


@router.post("/bets")
def add_bet(body: BetIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    game = conn.execute("SELECT id FROM games WHERE id=?", (body.game_id,)).fetchone()
    if not game:
        raise HTTPException(status_code=404, detail="game not found")
    cur = conn.execute(
        """
        INSERT INTO bets(game_id, market, side, line, price, stake, model_prob, ev, placed_at, note)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            body.game_id,
            body.market,
            body.side,
            body.line,
            body.price,
            body.stake,
            body.model_prob,
            body.ev,
            _now(),
            body.note,
        ),
    )
    conn.commit()
    return {"id": int(cur.lastrowid)}


@router.get("/bets/export")
def export_bets(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """可再导入的台账（手机 / PC 共用同一份格式）。"""
    return betssync.export_payload(conn)


@router.post("/bets/import")
async def import_bets(request: Request, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """合并一份导出的台账：按 ref + 自然键去重，重复的跳过/单调合并。"""
    try:
        payload = await request.json()
    except Exception:  # noqa: BLE001
        raise HTTPException(status_code=400, detail="invalid JSON")
    result = betssync.import_rows(conn, payload)
    if (
        not result.get("added")
        and not result.get("updated")
        and not result.get("duplicates")
    ):
        raise HTTPException(status_code=400, detail="no valid bets found")
    return result


@router.patch("/bets/{bet_id}")
def edit_bet(bet_id: int, body: BetPatch, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """编辑注单。

    - 未结算：可改 market/side/line/price/stake/note（market 与 side 联动校验）；
    - 已结算：只允许改 result/profit/note（比赛已经打完，盘口/赔率的改动会破坏结算口径）；
    - result 改回 open 或在已结算注单上改 result 时，清掉 closing/CLV（那是结算时写入的）。
    """
    row = conn.execute("SELECT * FROM bets WHERE id=?", (bet_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="bet not found")
    bet = dict(row)
    settled = bet["result"] != "open"
    updates: dict = {}

    if body.market is not None:
        if settled:
            raise HTTPException(status_code=400, detail="settled bet: market cannot change")
        updates["market"] = body.market
    if body.side is not None:
        if settled:
            raise HTTPException(status_code=400, detail="settled bet: side cannot change")
        updates["side"] = body.side
    if body.line is not None:
        if settled:
            raise HTTPException(status_code=400, detail="settled bet: line cannot change")
        updates["line"] = body.line
    if body.price is not None:
        if settled:
            raise HTTPException(status_code=400, detail="settled bet: price cannot change")
        updates["price"] = body.price
    if body.stake is not None:
        updates["stake"] = body.stake
    if body.note is not None:
        updates["note"] = body.note

    if body.result is not None:
        updates["result"] = body.result
    if body.profit is not None:
        updates["profit"] = body.profit

    market = updates.get("market", bet["market"])
    side = updates.get("side", bet["side"])
    valid_sides = {"spread": ("home", "away"), "total": ("over", "under"), "moneyline": ("home", "away")}
    if side not in valid_sides.get(market, ()):
        raise HTTPException(status_code=400, detail=f"side {side} invalid for {market}")
    if market != "moneyline" and updates.get("line", bet["line"]) is None:
        raise HTTPException(status_code=400, detail=f"{market} requires a line")

    # 结果变化时：open → 结算（补盈亏）、结算 → open（清盈亏与收盘）
    new_result = updates.get("result", bet["result"])
    if "result" in updates and updates["result"] != bet["result"]:
        if updates["result"] == "open":
            updates["profit"] = None
            updates["closing_line"] = None
            updates["closing_price"] = None
            updates["clv"] = None
            updates["clv_line"] = None
        elif body.profit is None and bet["result"] == "open":
            # 手动标记结果但没给盈亏：按注额×赔率算一个
            stake = updates.get("stake", bet["stake"]) or 0.0
            price = updates.get("price", bet["price"]) or 1.0
            updates["profit"] = (
                stake * (price - 1.0) if new_result == "won" else -stake if new_result == "lost" else 0.0
            )
    if updates.get("stake") is not None and bet["result"] != "open" and body.profit is None:
        # 已结算注单改注额：盈亏按新注额重算（保持正负号语义）
        stake = updates["stake"]
        old_stake = bet["stake"] or 0.0
        old_profit = bet["profit"] or 0.0
        scale = stake / old_stake if old_stake else 0.0
        updates["profit"] = round(old_profit * scale, 2)

    if not updates:
        raise HTTPException(status_code=400, detail="nothing to update")
    assignments = ", ".join(f"{name}=?" for name in updates)
    conn.execute(
        f"UPDATE bets SET {assignments} WHERE id=?",
        list(updates.values()) + [bet_id],
    )
    conn.commit()
    return {"ok": True, "id": bet_id, "updated": updates}


@router.get("/bets/summary")
def bets_summary(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    return summarize(conn)


@router.get("/bets")
def list_bets(
    game_id: str | None = None,
    result: str | None = None,
    conn: sqlite3.Connection = Depends(get_conn),
) -> dict:
    sql = """
        SELECT b.*, g.slate_date,
               h.abbrev AS home_abbrev, a.abbrev AS away_abbrev
        FROM bets b
        JOIN games g ON g.id = b.game_id
        JOIN teams h ON h.id = g.home_id
        JOIN teams a ON a.id = g.away_id
    """
    conditions, params = [], []
    if game_id:
        conditions.append("b.game_id=?")
        params.append(game_id)
    if result:
        conditions.append("b.result=?")
        params.append(result)
    if conditions:
        sql += " WHERE " + " AND ".join(conditions)
    sql += " ORDER BY b.id DESC LIMIT 200"
    rows = conn.execute(sql, params).fetchall()
    return {"bets": [dict(row) for row in rows]}


@router.delete("/bets/{bet_id}")
def delete_bet(bet_id: int, force: bool = False, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """撤销纸面注单。

    未结算：直接删（记错了很常见）。
    已结算：需要 force=true —— 破坏历史样本要显式确认；用于清除完全错误的记录。
    """
    row = conn.execute("SELECT id, result, ref FROM bets WHERE id=?", (bet_id,)).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="bet not found")
    if row["result"] != "open" and not force:
        raise HTTPException(
            status_code=400,
            detail="settled bets cannot be deleted without force=true",
        )
    conn.execute("DELETE FROM bets WHERE id=?", (bet_id,))
    conn.commit()
    try:
        betssync.record_deletions([str(row["ref"] or f"srv-{bet_id}")])
    except Exception:  # noqa: BLE001 - 坚果云不可用时仍以本地删除为准
        pass
    return {"ok": True, "id": bet_id, "deleted_ref": row["ref"]}


class ClearBetsIn(BaseModel):
    """清空台账的范围与确认。"""

    scope: Literal["all", "backfill"] = "all"
    confirm: str  # 必须输入 CLEAR / 回填 才执行


@router.post("/bets/clear")
def clear_bets(body: ClearBetsIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """清空台账（危险操作，双重确认）。

    scope:
      - "all"：删除全部注单（confirm 必须为 "CLEAR"）；
      - "backfill"：只删回填的模拟注单（note LIKE '回填%'，confirm 必须为 "回填"），
        用于重新回填前清样本。
    清空前自动落一份 JSON 备份到 data/backup/（bets-clear-<ts>.json）。
    """
    expected = "CLEAR" if body.scope == "all" else "回填"
    if body.confirm != expected:
        raise HTTPException(status_code=400, detail=f"确认文本不符，请输入 {expected}")
    if body.scope == "backfill":
        rows = conn.execute("SELECT * FROM bets WHERE note LIKE '回填%'").fetchall()
    else:
        rows = conn.execute("SELECT * FROM bets").fetchall()
    refs = [str(r["ref"] or f"srv-{r['id']}") for r in rows]

    # 清空先备份（本机留档，防止手滑）
    if rows:
        try:
            backup_dir = DATA_DIR / "backup"
            backup_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
            payload = {
                "schema": "nbaq.bets/1",
                "cleared_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "scope": body.scope,
                "count": len(rows),
                "bets": [dict(r) for r in rows],
            }
            (backup_dir / f"bets-clear-{ts}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except Exception:  # noqa: BLE001 - 备份失败不阻塞清空
            pass

    if body.scope == "backfill":
        cur = conn.execute("DELETE FROM bets WHERE note LIKE '回填%'")
    else:
        cur = conn.execute("DELETE FROM bets")
    conn.commit()

    # 写删除墓碑：否则云端台账/其他设备下一轮会把它们合并回来
    # 模拟单额外记录「同场同市场」的新式 ref，防止自动回填再次生成
    tombstoned = 0
    try:
        from .jobs.simulate import sim_ref

        extra = [
            sim_ref(r["game_id"], r["market"])
            for r in rows
            if "模拟" in (r["note"] or "") or "回填" in (r["note"] or "")
        ]
        tombstoned = betssync.record_deletions(refs + extra)
    except Exception:  # noqa: BLE001 - 坚果云不可用时仍以本地清空为准
        pass

    return {
        "ok": True,
        "deleted": cur.rowcount,
        "backed_up": len(rows),
        "tombstoned": tombstoned,
    }


@router.get("/bets/analysis")
def bets_analysis(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Bankroll summary + cumulative profit curve + bet ledger (joined with games).

    额外带上比赛状态与实时比分（live_home_score/live_away_score），供收益页给
    未结算注单标注「目前赢盘/输盘/贴盘」——API 模式与静态模式同口径。
    """
    summary = summarize(conn)
    rows = conn.execute(
        """
        SELECT b.*, g.slate_date,
               g.status AS game_status,
               g.live_home_score, g.live_away_score,
               g.home_score AS final_home_score, g.away_score AS final_away_score,
               h.abbrev AS home_abbrev, a.abbrev AS away_abbrev
        FROM bets b
        LEFT JOIN games g ON g.id = b.game_id
        LEFT JOIN teams h ON h.id = g.home_id
        LEFT JOIN teams a ON a.id = g.away_id
        ORDER BY b.id
        """
    ).fetchall()

    bets = []
    for row in rows:
        item = dict(row)
        home = item.pop("home_abbrev", None)
        away = item.pop("away_abbrev", None)
        item["matchup"] = f"{away or '?'} @ {home or '?'}" if home or away else item["game_id"]
        item["matchup_zh"] = i18n.matchup_zh(item["matchup"])
        # 赛前 / 滚球 / 回填（细分）与 真实 / 模拟（主口径）
        item["kind"] = bet_kind(item)
        item["source"] = bet_source(item)
        bets.append(item)

    # 累计收益曲线：整体 + 真实/模拟分开。
    # normalize=True 时把每笔盈亏换算成「每注 1 单位」，排除仓位大小的影响。
    def _curve(items: list[dict], normalize: bool = False) -> list[dict]:
        decided = [
            b for b in items if b["result"] in ("won", "lost", "push") and b["slate_date"]
        ]
        decided.sort(key=lambda b: (b["slate_date"], b["id"]))
        out: list[dict] = []
        running = 0.0
        for bet in decided:
            step = bet["profit"] or 0.0
            if normalize:
                stake = bet["stake"] or 0.0
                step = (step / stake) if stake else 0.0
            running += step
            if out and out[-1]["date"] == bet["slate_date"]:
                out[-1]["cum_profit"] = round(running, 4 if normalize else 2)
                out[-1]["n"] += 1
            else:
                out.append(
                    {
                        "date": bet["slate_date"],
                        "cum_profit": round(running, 4 if normalize else 2),
                        "n": 1,
                    }
                )
        return out

    def _curves(items: list[dict], normalize: bool = False) -> dict:
        return {
            "mine": _curve([b for b in items if b["source"] == "mine"], normalize),
            "sim": _curve([b for b in items if b["source"] == "sim"], normalize),
        }

    return {
        "summary": summary,
        "curve": _curve(bets),
        "curves": _curves(bets),
        "curves_unit": _curves(bets, normalize=True),
        "bets": bets,
    }


class ImpactIn(BaseModel):
    impact: float


@router.post("/injuries/{injury_id}/impact")
def set_impact(injury_id: int, body: ImpactIn, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    conn.execute("UPDATE injuries SET impact=? WHERE id=?", (body.impact, injury_id))
    conn.commit()
    return {"ok": True}


@router.post("/injuries/{injury_id}/apply-suggestion")
def apply_impact_suggestion(injury_id: int, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """把 RAPM 建议仓位写入该伤停的 impact（一键采用）。"""
    from .models import rapm as rapm_module

    injury = conn.execute("SELECT id, game_id, player FROM injuries WHERE id=?", (injury_id,)).fetchone()
    if not injury:
        raise HTTPException(status_code=404, detail="injury not found")
    suggestions = rapm_module.suggest_impacts(conn, injury["game_id"])
    match = next((s for s in suggestions if s["player"] == injury["player"]), None)
    if not match:
        raise HTTPException(status_code=404, detail="no RAPM suggestion for this player")
    conn.execute("UPDATE injuries SET impact=? WHERE id=?", (match["suggested_impact"], injury_id))
    conn.commit()
    return {"ok": True, "impact": match["suggested_impact"], "rapm_rating": match["rapm_rating"]}


@router.get("/model")
def model_info(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    row = conn.execute("SELECT * FROM models ORDER BY id DESC LIMIT 1").fetchone()
    if not row:
        return {"model": None}
    params = json.loads(row["params"])
    return {
        "created_at": row["created_at"],
        "n_games": row["n_games"],
        "sigma_margin": row["sigma_margin"],
        "sigma_total": row["sigma_total"],
        "margin_intercept": params.get("margin_intercept"),
        "margin_elo": params.get("margin_elo"),
        "margin_rest": params.get("margin_rest"),
        "total_intercept": params.get("total_intercept"),
        "teams": params.get("teams", {}),
    }


@router.get("/backtest")
def backtest_report() -> dict:
    path = DATA_DIR / "backtest_report.json"
    if not path.exists():
        return {"report": None}
    return {"report": json.loads(path.read_text(encoding="utf-8"))}


@router.get("/dates")
def game_dates(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """All slate dates that have games (for auto-jumping off-days)."""
    rows = conn.execute(
        """
        SELECT slate_date, COUNT(*) AS n FROM games
        WHERE status NOT IN ('STATUS_POSTPONED', 'STATUS_CANCELED', 'STATUS_CANCELLED')
        GROUP BY slate_date ORDER BY slate_date
        """
    ).fetchall()
    return {"dates": [{"date": row["slate_date"], "n": row["n"]} for row in rows]}


@router.get("/live")
def live_get(date: str | None = None, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Live scoreboard merged with pregame predictions (fetches ESPN)."""
    slate = date or current_slate_date()
    try:
        return live.live_board(conn, slate)
    except Exception as exc:  # noqa: BLE001 - upstream errors vary
        raise HTTPException(status_code=502, detail=f"ESPN live fetch failed: {exc}") from exc


@router.post("/live/refresh")
def live_refresh(date: str | None = None, conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """手动刷新赛时数据：重新 ingest 比分/状态 + 抓滚球盘口 + 记录胜率轨迹。

    赛时页的「刷新」按钮用它——watch 每 15 分钟才跑一轮，刚开赛那几分钟
    滚球盘口可能还没入库，手动点一下可以立刻补上。
    """
    slate = date or current_slate_date()
    stats: dict = {"date": slate}

    try:
        from .ingest import service as ingest_service

        stats["ingest"] = ingest_service.ingest_date(slate)
    except Exception as exc:  # noqa: BLE001
        stats["ingest_error"] = str(exc)

    try:
        from .ingest import hao

        stats["live_odds"] = hao.store_live_odds(conn, slate)
    except Exception as exc:  # noqa: BLE001
        stats["live_odds_error"] = str(exc)

    try:
        from .models.live_wp import record_snapshot

        stats["live_wp"] = record_snapshot(conn, slate)
    except Exception as exc:  # noqa: BLE001
        stats["live_wp_error"] = str(exc)

    return stats


@router.get("/live-wp")
def live_wp_report() -> dict:
    """赛中胜率模型验收报告（研究模块，不产生下注信号）。"""
    from .models.live_wp import load_params, load_report

    report = load_report()
    if report is None:
        return {"report": None, "params": load_params()}
    return {"report": report, "params": load_params()}


@router.get("/live-wp/{game_id}")
def live_wp_trajectory(game_id: str, limit: int = 400) -> dict:
    """该场已记录的赛中胜率轨迹（watch 每轮写入）。"""
    from .models.live_wp import load_params

    conn = db.connect()
    try:
        rows = conn.execute(
            """
            SELECT period, clock, home_score, away_score, margin,
                   seconds_remaining, home_win_prob, captured_at
            FROM live_wp WHERE game_id=? ORDER BY id LIMIT ?
            """,
            (game_id, max(1, min(limit, 2000))),
        ).fetchall()
    finally:
        conn.close()
    return {
        "game_id": game_id,
        "count": len(rows),
        "params": load_params(),
        "points": [dict(r) for r in rows],
    }


@router.get("/live-odds/{game_id}")
def live_odds_snapshots(game_id: str, limit: int = 500) -> dict:
    """该场已记录的滚球盘口原始快照（研究模块：仅原样返回，未做语义解释）。"""
    conn = db.connect()
    try:
        rows = conn.execute(
            """
            SELECT period, clock, score, market_raw, suspended, captured_at
            FROM live_odds WHERE game_id=? ORDER BY id LIMIT ?
            """,
            (game_id, max(1, min(limit, 3000))),
        ).fetchall()
    finally:
        conn.close()
    points = []
    for r in rows:
        item = dict(r)
        item.pop("market_raw", None)
        item.pop("detail_raw", None)
        item["suspended"] = bool(item.get("suspended"))
        points.append(item)
    return {"game_id": game_id, "count": len(points), "points": points}


@router.get("/model-clv")
def model_clv(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """模型 vs 收盘线历史检验报告（基准对照）。"""
    path = DATA_DIR / "model_clv_report.json"
    if not path.exists():
        return {"report": None}
    return {"report": json.loads(path.read_text(encoding="utf-8"))}


@router.get("/linemove")
def linemove() -> dict:
    """信号→线移动方向检验报告（信号是否预测线移动）。"""
    path = DATA_DIR / "linemove_report.json"
    if not path.exists():
        return {"overall": None, "by_market": {}}
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/holdout")
def holdout() -> dict:
    """冻结 holdout 验证报告（前 70% 训练 → 后 30% 一次性考试）。"""
    path = DATA_DIR / "holdout_report.json"
    if not path.exists():
        return {"error": "no holdout report; run 'nbaq holdout'"}
    return json.loads(path.read_text(encoding="utf-8"))


@router.get("/trends")
def trends(conn: sqlite3.Connection = Depends(get_conn)) -> dict:
    """Monthly league scoring/margin environment (regular season only)."""
    rows = conn.execute(
        """
        SELECT substr(slate_date, 1, 7) AS month,
               AVG(home_score + away_score) AS avg_total,
               AVG(ABS(home_score - away_score)) AS avg_abs_margin,
               COUNT(*) AS n
        FROM games
        WHERE home_score IS NOT NULL AND season_type = 2
        GROUP BY month
        HAVING n >= 5
        ORDER BY month
        """
    ).fetchall()
    return {
        "months": [row["month"] for row in rows],
        "avg_total": [round(row["avg_total"], 2) for row in rows],
        "avg_abs_margin": [round(row["avg_abs_margin"], 2) for row in rows],
        "n": [row["n"] for row in rows],
    }
