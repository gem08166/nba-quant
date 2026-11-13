"""公允锚审计（P0）：锚本身校准吗？三条口径，全部只读。

背景（2026-11-13，CONTEXT §10.81-10.82）：CLV 检验证明"模型 edge 系统性高估"
之后，需要把责任划分清楚——是模型概率的问题还是公允锚的问题。本脚本回答：

1. **条件审计**（signal_log）：模型选中侧的 p_fair vs 实际结果。
   样本=信号行（有选择偏差），但直接对应当年真实会下注的集合。
2. **无条件审计**（predictions.details）：全部完赛比赛、开赛前最后一版预测
   的所有 priced 边。聚合数把双边都算了（两边必有一赢一输，机械拉平），
   真正的信息在分桶与单侧（home/over）口径。
3. **去水方法对比**（--devig-compare，可选）：对 odds_snapshots 重算
   multiplicative vs shin 两种公允，分桶对比尾部偏差与 Brier。

验收口径：reliability（分桶差值）、Brier vs 恒猜基线、
锚隐含 ROI vs 实际 ROI（锚的定价能力）。

用法：
    python -m scripts.anchor_audit [--db PATH] [--devig-compare]
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db  # noqa: E402
from app.jobs.signal_backtest import settle_edge  # noqa: E402
from app.pricing.devig import fair_pair, fair_two_way  # noqa: E402

BINS = [(0.20, 0.35), (0.35, 0.45), (0.45, 0.55), (0.55, 0.65), (0.65, 0.80), (0.80, 1.01)]


def _ci95(hit: float, n: int) -> float:
    return 1.96 * math.sqrt(hit * (1 - hit) / n) if n else 0.0


def _bins(items: list[dict], key: str, min_n: int = 5) -> None:
    for lo, hi in BINS:
        b = [r for r in items if r[key] is not None and lo <= r[key] < hi]
        if len(b) < min_n:
            continue
        h = sum(r["won"] for r in b) / len(b)
        m = sum(r[key] for r in b) / len(b)
        flag = "  <-- 偏" if abs(m - h) > 0.08 and len(b) >= 30 else ""
        print(f"    {lo:.2f}-{hi:.2f}: n={len(b):<4} p={m:.3f} hit={h:.3f} 差 {m - h:+.3f}{flag}")


def _brier(items: list[dict], key: str) -> tuple[float, int]:
    sub = [r for r in items if r.get(key) is not None]
    if not sub:
        return float("nan"), 0
    return sum((r[key] - r["won"]) ** 2 for r in sub) / len(sub), len(sub)


# ---------------------------------------------------------------- 条件审计
def audit_conditional(conn: sqlite3.Connection) -> None:
    rows = [dict(r) for r in conn.execute(
        "SELECT game_id, market, side, p_fair, p_model, price_consensus, price_best, "
        "result FROM signal_log WHERE result IN ('won','lost') ORDER BY captured_at, id"
    )]
    seen: set[tuple] = set()
    out: list[dict] = []
    for r in rows:
        key = (r["game_id"], r["market"], r["side"])
        if key in seen:
            continue
        seen.add(key)
        out.append({**r, "won": 1 if r["result"] == "won" else 0})
    if not out:
        print("条件审计: signal_log 无已结算行")
        return
    print(f"\n=== 条件审计（signal_log 信号选中侧, 去重后 {len(out)} 条）===")
    for key, label in (("p_fair", "公允锚 p_fair"), ("p_model", "模型 p_model")):
        sub = [r for r in out if r.get(key) is not None]
        if not sub:
            continue
        n = len(sub)
        hit = sum(r["won"] for r in sub) / n
        mp = sum(r[key] for r in sub) / n
        bs, _ = _brier(sub, key)
        print(f"[{label}] n={n} 平均 {mp:.3f} vs 实际 {hit:.3f} 差 {mp - hit:+.3f} "
              f"(±{_ci95(hit, n):.3f}) Brier {bs:.4f}")
        _bins(sub, key)
    fair_sub = [r for r in out if r.get("p_fair") is not None]
    if fair_sub:
        imp = act = 0.0
        n = 0
        for r in fair_sub:
            pc = r.get("price_consensus") or r.get("price_best")
            if not pc:
                continue
            imp += r["p_fair"] * pc - 1.0
            act += (pc - 1.0) if r["won"] else -1.0
            n += 1
        if n:
            print(f"[定价能力] n={n} 锚隐含 ROI {imp / n:+.2%} vs 实际 {act / n:+.2%} "
                  f"差 {(imp - act) / n:+.2%}")


# -------------------------------------------------------------- 无条件审计
def _last_pre_tipoff_edges(conn: sqlite3.Connection) -> list[dict]:
    games = conn.execute(
        "SELECT id, tipoff, home_score, away_score FROM games "
        "WHERE status='STATUS_FINAL' AND home_score IS NOT NULL AND away_score IS NOT NULL"
    ).fetchall()
    rows: list[dict] = []
    for g in games:
        pred = conn.execute(
            "SELECT details FROM predictions WHERE game_id=? AND created_at <= ? "
            "ORDER BY created_at DESC, id DESC LIMIT 1",
            (g["id"], g["tipoff"]),
        ).fetchone()
        if pred is None:
            continue
        try:
            details = json.loads(pred["details"] or "{}")
        except (TypeError, ValueError):
            continue
        for e in details.get("edges") or []:
            fair = e.get("fair_prob")
            if fair is None or not (0.0 < float(fair) < 1.0):
                continue
            result = settle_edge(e["market"], e["side"], e.get("line"),
                                 int(g["home_score"]), int(g["away_score"]))
            if result == "push":
                continue
            rows.append({
                "market": e["market"], "side": e["side"],
                "fair": float(fair), "model": e.get("model_prob"),
                "won": 1 if result == "won" else 0,
            })
    return rows


def audit_unconditional(conn: sqlite3.Connection) -> None:
    rows = _last_pre_tipoff_edges(conn)
    if not rows:
        print("\n=== 无条件审计: 无可用预测 ===")
        return
    print(f"\n=== 无条件审计（开赛前最后一版预测的所有 priced 边, {len(rows)} 条）===")
    print("（聚合数把双边都算了——两边必有一赢一输，机械拉平；看分桶与单侧）")
    for key, label in (("fair", "公允锚 fair"), ("model", "模型 model")):
        sub = [r for r in rows if r.get(key) is not None]
        n = len(sub)
        hit = sum(r["won"] for r in sub) / n
        mp = sum(r[key] for r in sub) / n
        bs, _ = _brier(sub, key)
        print(f"[{label}] n={n} 平均 {mp:.3f} vs {hit:.3f} 差 {mp - hit:+.3f} "
              f"(±{_ci95(hit, n):.3f}) Brier {bs:.4f}")
        _bins(sub, key)
    # 单侧（home/over）：无选择偏差的聚合口径
    single = [r for r in rows if (r["market"] == "total" and r["side"] == "over")
              or (r["market"] in ("spread", "moneyline") and r["side"] == "home")]
    if single:
        n = len(single)
        hit = sum(r["won"] for r in single) / n
        mp = sum(r["fair"] for r in single) / n
        bs, _ = _brier(single, "fair")
        print(f"[单侧 home/over] n={n} 平均 fair {mp:.3f} vs {hit:.3f} 差 {mp - hit:+.3f} "
              f"(±{_ci95(hit, n):.3f}) Brier {bs:.4f}")
        for mkt in ("spread", "total", "moneyline"):
            sub = [r for r in single if r["market"] == mkt]
            if not sub:
                continue
            nn = len(sub)
            h = sum(r["won"] for r in sub) / nn
            m = sum(r["fair"] for r in sub) / nn
            print(f"    {mkt:>9}: n={nn:<4} p={m:.3f} hit={h:.3f} 差 {m - h:+.3f} (±{_ci95(h, nn):.3f})")
    # ML 热门/冷门侧（favorite-longshot 检验）
    ml = [r for r in rows if r["market"] == "moneyline"]
    for name, sub in (("热门侧 fair≥0.5", [r for r in ml if r["fair"] >= 0.5]),
                      ("冷门侧 fair<0.5", [r for r in ml if r["fair"] < 0.5])):
        if len(sub) < 10:
            continue
        n = len(sub)
        h = sum(r["won"] for r in sub) / n
        m = sum(r["fair"] for r in sub) / n
        print(f"    ML {name}: n={n:<4} p={m:.3f} hit={h:.3f} 差 {m - h:+.3f} (±{_ci95(h, n):.3f})")


# ---------------------------------------------------------- 去水方法对比
def audit_devig_compare(conn: sqlite3.Connection) -> None:
    SIDES = {"spread": ("home", "away"), "total": ("over", "under"), "moneyline": ("home", "away")}
    games = conn.execute(
        "SELECT id, tipoff, home_score, away_score FROM games "
        "WHERE status='STATUS_FINAL' AND home_score IS NOT NULL AND away_score IS NOT NULL"
    ).fetchall()
    rows_mult: list[dict] = []
    rows_shin: list[dict] = []
    for g in games:
        snaps = conn.execute(
            "SELECT book, market, side, line, price FROM odds_snapshots "
            "WHERE game_id=? AND captured_at <= ? AND price > 1.0",
            (g["id"], g["tipoff"]),
        ).fetchall()
        latest: dict[tuple, dict] = {}
        for s in snaps:
            key = (s["book"].strip().lower(), s["market"])
            latest.setdefault(key, {})[s["side"]] = (s["line"], s["price"])
        for (book, market), d in latest.items():
            sa, sb = SIDES.get(market, (None, None))
            if not sa or sa not in d or sb not in d:
                continue
            (la, pa), (lb, pb_) = d[sa], d[sb]
            if market == "spread":
                if la is None or lb is None or abs(la + lb) > 1e-9:
                    continue
            elif (la or 0) != (lb or 0):
                continue
            pair_m = fair_two_way(pa, pb_)
            pair_s = fair_pair(pa, pb_, method="shin")
            if not pair_m or not pair_s:
                continue
            for side, pm, ps in ((sa, pair_m[0], pair_s[0]), (sb, pair_m[1], pair_s[1])):
                line = la if (market != "spread" or side == sa) else lb
                result = settle_edge(market, side, line, int(g["home_score"]), int(g["away_score"]))
                if result == "push":
                    continue
                won = 1 if result == "won" else 0
                rows_mult.append({"market": market, "fair": pm, "won": won})
                rows_shin.append({"market": market, "fair": ps, "won": won})
    if not rows_mult:
        print("\n=== 去水对比: 无可用快照 ===")
        return
    print(f"\n=== 去水方法对比（同一批快照重算, {len(rows_mult)} 边）===")
    for rows, label in ((rows_mult, "multiplicative"), (rows_shin, "shin")):
        n = len(rows)
        bs = sum((r["fair"] - r["won"]) ** 2 for r in rows) / n
        print(f"[{label}] n={n} Brier {bs:.4f}")
        _bins(rows, "fair", min_n=10)
    for rows, label in ((rows_mult, "multiplicative"), (rows_shin, "shin")):
        ml = [r for r in rows if r["market"] == "moneyline"]
        for name, sub in (("热门", [r for r in ml if r["fair"] >= 0.5]),
                          ("冷门", [r for r in ml if r["fair"] < 0.5])):
            if len(sub) < 10:
                continue
            n = len(sub)
            h = sum(r["won"] for r in sub) / n
            m = sum(r["fair"] for r in sub) / n
            print(f"    ML-{label}-{name}: n={n:<4} p={m:.3f} hit={h:.3f} 差 {m - h:+.3f}")


def main() -> None:
    ap = argparse.ArgumentParser(description="公允锚审计（只读）")
    ap.add_argument("--db", default=None, help="alternate sqlite path")
    ap.add_argument("--devig-compare", action="store_true", help="附加去水方法对比")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db or str(db.DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        audit_conditional(conn)
        audit_unconditional(conn)
        if args.devig_compare:
            audit_devig_compare(conn)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
