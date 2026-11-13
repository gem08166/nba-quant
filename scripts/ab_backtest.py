"""A/B 两条改进路线的离线验收（只读，不改库）。

数据：`signal_log`（决策时刻快照 + 赛后回填的收盘价与结果）。
- **B（价格闸门）**：fair × price_consensus − 1 ≥ 阈值才下注。不依赖拟合，
  对全部历史行都是样本外。
- **A（概率校准）**：对 p_model 做 Platt 校准（含向 0.5 收缩），用校准后的
  概率重算 edge/EV 再判定信号。校准参数在**全样本**上拟合 → A 的结果是
  样本内（偏乐观），脚本会明确标注。

每注按 1u、用「能拿到的共识中位价」结算 ROI；同时报告 CLV
（决策价 / 收盘价 − 1）。样本很小（约 70 注），只看方向，不做显著性结论。

用法：
    python -m scripts.ab_backtest [--price best|consensus]
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.config import EDGE_THRESHOLD, MIN_EV, MIN_MARKET_EV
from app.pricing import calibrate as cal

B_PRICE_GATE = MIN_MARKET_EV


def load_rows(conn: sqlite3.Connection) -> list[dict]:
    """已结算的信号行；同一 (game, market, side) 只保留最早一次决策。"""
    rows = conn.execute(
        """
        SELECT s.*, g.tipoff, g.slate_date
        FROM signal_log s JOIN games g ON g.id = s.game_id
        WHERE s.result IN ('won', 'lost')
        ORDER BY s.captured_at, s.id
        """
    ).fetchall()
    seen: set[tuple] = set()
    out: list[dict] = []
    for row in rows:
        key = (row["game_id"], row["market"], row["side"])
        if key in seen:
            continue
        seen.add(key)
        out.append(dict(row))
    return out


def fit_calibrators(rows: list[dict]) -> dict[str, dict]:
    pairs: dict[str, list[tuple[float, int]]] = {}
    for row in rows:
        p = row.get("p_model")
        if p is None or not (0.0 < float(p) < 1.0):
            continue
        pairs.setdefault(row["market"], []).append((float(p), 1 if row["result"] == "won" else 0))
    out: dict[str, dict] = {}
    for market, plist in pairs.items():
        fit = cal.fit_platt(plist)
        if fit:
            out[market] = fit
    return out


def price_of(row: dict, mode: str) -> float | None:
    if mode == "best":
        return row.get("price_best")
    return row.get("price_consensus") or row.get("price_best")


def passes_b(row: dict) -> bool:
    fair = row.get("p_fair")
    price = row.get("price_consensus") or row.get("price_best")
    if not fair or not price:
        return False
    return float(fair) * float(price) - 1.0 >= B_PRICE_GATE


def passes_a(row: dict, cals: dict[str, dict]) -> bool:
    fair = row.get("p_fair")
    price = row.get("price_consensus") or row.get("price_best")
    p = row.get("p_model")
    if not fair or not price or p is None:
        return False
    q = cal.apply(cals.get(row["market"]), float(p))
    edge = q - float(fair)
    ev = q * float(price) - 1.0
    return edge >= EDGE_THRESHOLD and ev >= MIN_EV


def stats(rows: list[dict], mode: str) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    profit = 0.0
    clvs: list[float] = []
    for row in rows:
        price = price_of(row, mode) or 1.0
        if row["result"] == "won":
            profit += price - 1.0
        else:
            profit -= 1.0
        closing = row.get("price_closing")
        if closing and closing > 1.0 and price > 1.0:
            clvs.append(price / closing - 1.0)
    wins = sum(1 for r in rows if r["result"] == "won")
    return {
        "n": n,
        "hit": wins / n,
        "roi": profit / n,
        "clv": sum(clvs) / len(clvs) if clvs else None,
        "clv_n": len(clvs),
    }


def _fmt(label: str, s: dict) -> str:
    if not s.get("n"):
        return f"{label:<24} n=0"
    clv = f"{s['clv']:+.2%}" if s["clv"] is not None else "n/a"
    return (
        f"{label:<24} n={s['n']:<4} hit={s['hit']:.1%}  ROI={s['roi']:+.2%}  "
        f"CLV={clv} (n={s['clv_n']})"
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="A/B gate offline backtest")
    ap.add_argument("--price", choices=("best", "consensus"), default="consensus")
    ap.add_argument("--db", default=None, help="alternate sqlite path")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db or str(db.DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = load_rows(conn)
        cals = fit_calibrators(rows)
    finally:
        conn.close()

    print(f"rows={len(rows)}  price={args.price}  B_gate={B_PRICE_GATE:+.0%}  "
          f"EDGE={EDGE_THRESHOLD:.0%}  MIN_EV={MIN_EV:.0%}")
    for market, fit in sorted(cals.items()):
        print(f"  calibrate {market}: a={fit['a']} b={fit['b']} "
              f"(raw a={fit['raw_a']} w={fit['w']} n={fit['n']})")

    all_rows = rows
    b_rows = [r for r in rows if passes_b(r)]
    a_rows = [r for r in rows if passes_a(r, cals)]
    ab_rows = [r for r in rows if passes_b(r) and passes_a(r, cals)]

    print("\n-- 全样本（A 的校准参数在全样本上拟合 = 样本内，偏乐观）--")
    print("  " + _fmt("baseline (all signals)", stats(all_rows, args.price)))
    print("  " + _fmt(f"B only (price≥{B_PRICE_GATE:.0%})", stats(b_rows, args.price)))
    print("  " + _fmt("A only (calibrated edge)", stats(a_rows, args.price)))
    print("  " + _fmt("A + B", stats(ab_rows, args.price)))

    # 旧口径下的「可下注」子集，用于对比历史真实执行
    old = [r for r in rows if r.get("bettable")]
    print("\n-- 旧 bettable 子集（当年真的会下注的那些）--")
    print("  " + _fmt("old bettable", stats(old, args.price)))
    print("  " + _fmt("old bettable ∩ B", stats([r for r in old if passes_b(r)], args.price)))
    print("  " + _fmt("old bettable ∩ A+B", stats([r for r in old if passes_b(r) and passes_a(r, cals)], args.price)))


if __name__ == "__main__":
    main()
