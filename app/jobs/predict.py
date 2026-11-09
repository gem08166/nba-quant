from __future__ import annotations

import json
import os
import sqlite3
from datetime import datetime, timezone

from .. import db
from ..config import (
    BETTABLE_MIN_BOOKS,
    BETTABLE_PRICE_OUTLIER,
    EDGE_THRESHOLD,
    KELLY_FRACTION,
    MARKET_BLEND,
    MAX_STAKE_PCT,
    MIN_EV,
    MIN_FRESH_BOOKS,
    MUTUAL_EXCLUSIVE,
    SHARP_ANCHOR,
)
from ..models import rapm, ratings
from ..pricing.devig import (
    cover_prob,
    edge as edge_value,
    expected_value,
    fair_two_way,
    kelly_fraction,
    moneyline_prob,
    over_prob,
)

BOOK_PRIORITY = ("Draft Kings", "DraftKings", "ESPN BET", "BetMGM", "Caesars")

# Sharp 书源：定价效率高、软书会跟随其移动。SHARP_ANCHOR 开启时，
# fair 概率以 sharp 书去水为锚（软书价格只回答"哪里拿得到价"）。
SHARP_BOOKS = {"pinnacle"}
# 已知软书/聚合源：他们的"最优价"经常是延迟线或限注单，标记 sharp_doubt。
SOFT_BOOKS = {"1xbet", "betway", "smarkets", "unibet", "betrivers", "hao-tiyu", "bovada"}

# 书源别名归一化：同一 bookmaker 在不同数据源里的命名差异会被共识当成两家
# （实测 Draft Kings/DraftKings、FanDuel/fanduel 重复计权）。统一到规范名。
BOOK_ALIASES = {
    "draft kings": "draftkings",
    "draftkings": "draftkings",
    "fanduel": "fanduel",
    "fanatics": "fanatics",
    "betmgm": "betmgm",
    "bovada": "bovada",
    "betrivers": "betrivers",
    "bet_rivers_co": "betrivers",
    "caesars": "caesars",
    "espn bet": "espnbet",
    "1xbet": "1xbet",
    "betway": "betway",
    "smarkets": "smarkets",
    "unibet": "unibet",
    "pinnacle": "pinnacle",
}

# 快照超龄（小时）：共识优先用新鲜报价，陈旧价会让"最优价"失真。
# 若所有书源都超龄（数据源停更），回退全量并标记 stale——陈旧共识仍优于单书。
STALE_HOURS = float(os.getenv("NBAQ_STALE_HOURS", "7"))

# Never predict games that will never be played (postponed/canceled): they
# would otherwise hang on the board as "未开赛" forever and accumulate
# meaningless prediction rows.
SKIP_STATUSES = ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_CANCELLED")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def latest_odds(conn: sqlite3.Connection, game_id: str) -> list[dict]:
    """最新盘口快照（排除 SBR 收盘归档行——它们是赛后回填的历史数据，
    用于回测；混入会让对已完赛比赛的重预测产生后验信息泄漏）。"""
    rows = conn.execute(
        """
        SELECT * FROM odds_snapshots
        WHERE game_id=? AND book NOT LIKE 'sbr-%'
        ORDER BY captured_at, id
        """,
        (game_id,),
    ).fetchall()
    latest: dict[tuple[str, str, str], dict] = {}
    for row in rows:
        latest[(row["book"], row["market"], row["side"])] = dict(row)
    return list(latest.values())


def pick_book(rows: list[dict]) -> str | None:
    books = {row["book"] for row in rows}
    for book in BOOK_PRIORITY:
        if book in books:
            return book
    return sorted(books)[0] if books else None


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _normalize_book(book: str) -> str:
    return BOOK_ALIASES.get((book or "").strip().lower(), (book or "").strip().lower())


def _parse_ts_local(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _is_fresh(captured_at: str | None) -> bool:
    ts = _parse_ts_local(captured_at)
    if ts is None:
        return True
    return (_now_utc() - ts).total_seconds() < STALE_HOURS * 3600


def _seen_at(row: dict) -> str | None:
    """最后一次确认该报价仍有效的时间（旧库没有 last_seen_at 时退回 captured_at）。"""
    return row.get("last_seen_at") or row.get("captured_at")


def _devig_pair(price_a: float | None, price_b: float | None) -> tuple[float, float] | None:
    """乘法去水：返回 (a 侧公允概率, b 侧公允概率)。缺一边返回 None——
    单边价格无法去水，用它计算 edge 会产出假信号（审查 P0 项）。"""
    if not price_a or not price_b or price_a <= 1.0 or price_b <= 1.0:
        return None
    inv_a, inv_b = 1.0 / price_a, 1.0 / price_b
    total = inv_a + inv_b
    return inv_a / total, inv_b / total


def _apply_sharp_anchor(entries: list[dict], market: dict) -> dict:
    """Sharp 锚定：把共识公允概率换成 sharp 书源的去水值。

    全体平均公允概率会被软书迟滞线污染——真正的定价基准是 sharp 书
    （Pinnacle 类）。软书价格仍然保留用于"最优价/中位价"（回答哪里拿得到价），
    但 fair_* 换成 sharp 去水值，edge/EV 从此衡量"软书价 vs sharp 公允"。
    无 sharp 书源参与该市场时原样返回（自动回退全体共识）。
    """
    if not SHARP_ANCHOR:
        return market
    sharp = [e for e in entries if e["book"] in SHARP_BOOKS]
    if not sharp:
        return market
    fair_a = sum(e["fair_a"] for e in sharp) / len(sharp)
    fair_b = sum(e["fair_b"] for e in sharp) / len(sharp)
    out = dict(market)
    # 按 market dict 实际使用的键名写回
    for key_a, key_b in (("fair_home", "fair_away"), ("fair_over", "fair_under"), ("fair_a", "fair_b")):
        if key_a in out:
            out[key_a] = fair_a
            out[key_b] = fair_b
    out["sharp_anchor"] = True
    out["sharp_books"] = sorted({e["book"] for e in sharp})
    return out


def _mode(values: list[float]) -> float | None:
    """最常见线（并列取中位数）：多家书源让分/大小线可能不同。"""
    if not values:
        return None
    counts: dict[float, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    best = max(counts.values())
    tied = sorted(v for v, c in counts.items() if c == best)
    mid = len(tied) // 2
    return tied[mid] if len(tied) % 2 else (tied[mid - 1] + tied[mid]) / 2


def _robust_pair_stats(entries: list[dict], tol: float = BETTABLE_PRICE_OUTLIER):
    """鲁棒聚合：各书源去水公允概率，剔除与中位数偏差 > tol 的离群书源
    （防单源字段颠倒/坏数据污染共识，实测 hao-tiyu ML 反向案例），
    再取平均；价格为过滤后书源中的最优价，并记录其真实来源书名。"""
    if not entries:
        return 0.0, 0.0, None, None, 0, None, None, None
    med_a = sorted(e["fair_a"] for e in entries)[len(entries) // 2]
    med_b = sorted(e["fair_b"] for e in entries)[len(entries) // 2]
    kept = [
        e for e in entries
        if abs(e["fair_a"] - med_a) <= tol and abs(e["fair_b"] - med_b) <= tol
    ]
    if not kept:
        kept = entries
    n = len(kept)
    mean_a = sum(e["fair_a"] for e in kept) / n
    mean_b = sum(e["fair_b"] for e in kept) / n
    best_a_entry = max((e for e in kept if e["price_a"]), key=lambda e: e["price_a"], default=None)
    best_b_entry = max((e for e in kept if e["price_b"]), key=lambda e: e["price_b"], default=None)
    best_a = best_a_entry["price_a"] if best_a_entry else None
    best_b = best_b_entry["price_b"] if best_b_entry else None
    book_a = best_a_entry["book"] if best_a_entry else None
    book_b = best_b_entry["book"] if best_b_entry else None
    # 陈旧价检测：最优价的书源比其余书源平均更新时间明显更老 → 该价不可信
    stale_a = stale_b = False
    ages = [e.get("age_hours") for e in kept if e.get("age_hours") is not None]
    if ages and len(ages) >= 3:
        if best_a_entry and best_a_entry.get("age_hours") is not None:
            mean_age = sum(ages) / len(ages)
            stale_a = best_a_entry["age_hours"] > mean_age * 1.5 + 0.5
        if best_b_entry and best_b_entry.get("age_hours") is not None:
            mean_age = sum(ages) / len(ages)
            stale_b = best_b_entry["age_hours"] > mean_age * 1.5 + 0.5
    return mean_a, mean_b, best_a, best_b, n, book_a, book_b, (stale_a or stale_b)


def consensus_market(odds_rows: list[dict]) -> dict:
    """多书共识：对每家书源分别去水后取平均公允概率；线取众数；每侧取共识线下的最优价。

    书源名先归一化（同一 bookmaker 不同数据源的命名差异），超龄快照剔除；
    若剔除后无可用书源（数据源停更），回退到全量并标记 stale=True——
    陈旧共识仍优于 ESPN 单书。
    返回 {spread/total/moneyline: {line?, prices, fair probs, n_books, 来源书名}}。
    单书源时退化为该书源的去水结果（与旧逻辑兼容）。
    """
    books: dict[str, dict[tuple[str, str], dict]] = {}
    fresh_books: dict[str, dict[tuple[str, str], dict]] = {}
    now = _now_utc()
    for row in odds_rows:
        if not row.get("price") or row["price"] <= 1.0:
            continue
        book = _normalize_book(row.get("book"))
        key = (row["market"], row["side"])
        seen_at = _seen_at(row)
        age_hours = None
        ts = _parse_ts_local(seen_at)
        if ts:
            age_hours = (now - ts).total_seconds() / 3600.0
        entry = {"book": book, "market": row["market"], "side": row["side"],
                 "line": row.get("line"), "price": row["price"],
                 "captured_at": row.get("captured_at"), "age_hours": age_hours}
        target = books.setdefault(book, {})
        existing = target.get(key)
        if existing is None or (existing.get("age_hours") or 0) >= (age_hours or 0):
            target[key] = entry
        if _is_fresh(seen_at):
            ftarget = fresh_books.setdefault(book, {})
            fexisting = ftarget.get(key)
            if fexisting is None or (fexisting.get("age_hours") or 0) >= (age_hours or 0):
                ftarget[key] = entry

    def _books_for_market(market: str) -> tuple[dict, bool]:
        """新鲜书源足够多才只用新鲜的；否则回退全量并标记 stale。

        只按"有没有任何新鲜书源"做 all-or-nothing 切换会让共识在一条线变动后
        从 12 家退化成 1 家——单书共识的噪声远大于略旧的 12 家共识。
        """
        fresh_n = sum(
            1 for by_key in fresh_books.values()
            if any(key[0] == market for key in by_key)
        )
        if fresh_n >= MIN_FRESH_BOOKS:
            return fresh_books, False
        return books, True

    result: dict[str, dict] = {"stale": False, "min_fresh_books": MIN_FRESH_BOOKS}
    # --- spread（home 线） ---
    active_books, market_stale = _books_for_market("spread")
    result["stale"] = result["stale"] or market_stale
    entries: list[dict] = []
    for book, by_key in active_books.items():
        h, a = by_key.get(("spread", "home")), by_key.get(("spread", "away"))
        if not h or not a or h.get("line") is None or a.get("line") is None:
            continue
        line_h, line_a = float(h["line"]), float(a["line"])
        if abs(line_h + line_a) > 1e-9:  # 两边线应互为相反数
            continue
        pair = _devig_pair(h["price"], a["price"])
        if not pair:
            continue
        entries.append({"book": book, "line": line_h, "fair_h": pair[0], "fair_a": pair[1],
                        "price_h": h["price"], "price_a": a["price"],
                        "age_h": h.get("age_hours")})
    if entries:
        consensus_line = _mode([e["line"] for e in entries])
        at_line = [e for e in entries if abs(e["line"] - consensus_line) < 1e-9]
        mean_a, mean_b, best_h, best_a, n_kept, book_h, book_a, stale_best = _robust_pair_stats(
            [
                {"book": e["book"], "fair_a": e["fair_h"], "fair_b": e["fair_a"],
                 "price_a": e["price_h"], "price_b": e["price_a"], "age_hours": e["age_h"]}
                for e in at_line
            ]
        )
        spread_out = {
            "line": consensus_line,
            "home_price": best_h,
            "away_price": best_a,
            "fair_home": mean_a,
            "fair_away": mean_b,
            "n_books": n_kept,
            "home_book": book_h,
            "away_book": book_a,
            "best_price_stale": stale_best,
        }
        result["spread"] = _apply_sharp_anchor(at_line, spread_out)

    # --- total ---
    active_books, market_stale = _books_for_market("total")
    result["stale"] = result["stale"] or market_stale
    entries: list[dict] = []
    for book, by_key in active_books.items():
        o, u = by_key.get(("total", "over")), by_key.get(("total", "under"))
        if not o or not u or o.get("line") is None or u.get("line") is None:
            continue
        line_o, line_u = float(o["line"]), float(u["line"])
        if abs(line_o - line_u) > 1e-9:
            continue
        pair = _devig_pair(o["price"], u["price"])
        if not pair:
            continue
        entries.append({"book": book, "line": line_o, "fair_a": pair[0], "fair_b": pair[1],
                        "price_a": o["price"], "price_b": u["price"],
                        "age_h": o.get("age_hours"), "age_h_b": u.get("age_hours")})
    if entries:
        consensus_line = _mode([e["line"] for e in entries])
        at_line = [e for e in entries if abs(e["line"] - consensus_line) < 1e-9]
        mean_o, mean_u, best_o, best_u, n_kept, book_o, book_u, stale_best = _robust_pair_stats(
            [
                {"book": e["book"], "fair_a": e["fair_a"], "fair_b": e["fair_b"],
                 "price_a": e["price_a"], "price_b": e["price_b"], "age_hours": e["age_h"]}
                for e in at_line
            ]
        )
        total_out = {
            "line": consensus_line,
            "over_price": best_o,
            "under_price": best_u,
            "fair_over": mean_o,
            "fair_under": mean_u,
            "n_books": n_kept,
            "over_book": book_o,
            "under_book": book_u,
            "best_price_stale": stale_best,
        }
        result["total"] = _apply_sharp_anchor(at_line, total_out)

    # --- moneyline ---
    active_books, market_stale = _books_for_market("moneyline")
    result["stale"] = result["stale"] or market_stale
    ml_entries: list[dict] = []
    for book, by_key in active_books.items():
        h, a = by_key.get(("moneyline", "home")), by_key.get(("moneyline", "away"))
        if not h or not a:
            continue
        pair = _devig_pair(h["price"], a["price"])
        if not pair:
            continue
        ml_entries.append({"book": book, "fair_a": pair[0], "fair_b": pair[1],
                           "price_a": h["price"], "price_b": a["price"],
                           "age_h": h.get("age_hours")})
    if ml_entries:
        mean_h, mean_a, best_h, best_a, n_kept, book_h, book_a, stale_best = _robust_pair_stats(ml_entries)
        # 共识中位价（EV 计算用——最优价常有跨书价差，模型 EV 应按"可确定拿到的
        # 价格"评估，而非碰运气的全场最优；最优价仍保留展示用于比较）。
        median_h = sorted(e["price_a"] for e in ml_entries)[len(ml_entries) // 2]
        median_a = sorted(e["price_b"] for e in ml_entries)[len(ml_entries) // 2]
        ml_out = {
            "home_price": best_h,
            "away_price": best_a,
            "home_price_median": median_h,
            "away_price_median": median_a,
            "fair_home": mean_h,
            "fair_away": mean_a,
            "n_books": n_kept,
            "home_book": book_h,
            "away_book": book_a,
            "best_price_stale": stale_best,
        }
        result["moneyline"] = _apply_sharp_anchor(ml_entries, ml_out)
    return result


def _side(by_key: dict[tuple[str, str], dict], market: str, side: str) -> dict | None:
    return by_key.get((market, side))


def _make_edge(
    market: str,
    side: str,
    line: float | None,
    price: float | None,
    model_prob: float,
    fair_prob: float,
    book: str,
    created_at: str,
    ev_price: float | None = None,
) -> dict | None:
    """edge 用共识公允概率；EV 用 ev_price（共识中位价——可确定拿到的价格），
    展示价 price 保留最优价用于比较。"""
    if price is None or price <= 1.0:
        return None
    ev_p = ev_price or price
    ev = expected_value(model_prob, ev_p)
    edge = edge_value(model_prob, fair_prob)
    kelly = kelly_fraction(model_prob, ev_p)
    return {
        "market": market,
        "side": side,
        "line": line,
        "price": price,
        "book": book,
        "model_prob": model_prob,
        "fair_prob": fair_prob,
        "edge": edge,
        "ev": ev,
        "kelly": kelly,
        "stake_pct": min(kelly * KELLY_FRACTION, MAX_STAKE_PCT),
        "signal": bool(edge >= EDGE_THRESHOLD and ev >= MIN_EV and kelly > 0),
        "created_at": created_at,
    }


def _bettable_reason(edge: dict, n_books: int, best_book: str) -> str | None:
    """可下注闸门：signal 只说明"模型算出来有 EV"，bettable 说明"这个价真实
    可拿、共识足够深"。返回 None=可下注；返回字符串=降级原因。

    与 signal 分开的理由：信号的历史 ROI 是 −11%，其中最大 edge 分位最差
    （−27.7%）——主要死因是"最优价其实是延迟线/限注单"。这几条闸门直接
    针对该死因，宁可少不可假。
    """
    if best_book and best_book.lower() in SOFT_BOOKS:
        return f"最优价来自软书/聚合源 {best_book}（延迟线/限注单风险）"
    if n_books < BETTABLE_MIN_BOOKS:
        return f"共识书源数 {n_books} < {BETTABLE_MIN_BOOKS}（定价基准不可靠）"
    return None


def build_market(
    pred: dict,
    odds_rows: list[dict],
    created_at: str | None = None,
) -> tuple[str | None, dict]:
    """定价：多书共识（多书源各自去水取平均公允概率、线取众数、每侧取共识线下最优价），
    再与模型按 MARKET_BLEND 融合。只有单书源时自动退化为该书源。"""
    created_at = created_at or _now()
    cons = consensus_market(odds_rows)
    book = pick_book(odds_rows)
    by_key = {(row["market"], row["side"]): row for row in odds_rows if row["book"] == book}
    model_mu_m = pred["pred_margin"]
    model_mu_t = pred["pred_total"]
    sigma_m = pred["sigma_margin"]
    sigma_t = pred["sigma_total"]

    spread_cons = cons.get("spread")
    spread_line = spread_cons["line"] if spread_cons else None
    total_cons = cons.get("total")
    total_line = total_cons["line"] if total_cons else None

    home_spread = _side(by_key, "spread", "home")
    away_spread = _side(by_key, "spread", "away")
    over_row = _side(by_key, "total", "over")
    under_row = _side(by_key, "total", "under")
    home_ml = _side(by_key, "moneyline", "home")
    away_ml = _side(by_key, "moneyline", "away")

    # Blend the model with the consensus market line. Single soft book lines are
    # noisy and early-season team ratings are stale, so a pure model would
    # generate far too many fake edges.
    pricing_mu_m = model_mu_m
    pricing_mu_t = model_mu_t
    market_mu_m = -spread_line if spread_line is not None else None
    if market_mu_m is not None:
        pricing_mu_m = MARKET_BLEND * model_mu_m + (1.0 - MARKET_BLEND) * market_mu_m
    if total_line is not None:
        pricing_mu_t = MARKET_BLEND * model_mu_t + (1.0 - MARKET_BLEND) * total_line

    probabilities: dict[str, float] = {}
    edges: list[dict] = []

    # 分歧闸门（P0-5 修正版）：分差分歧只拦分差市场，不再跨市场连坐——
    # PHI@DET 的 total 与分差分歧无关（220.4 vs 218.5 仅差 1.9），却被旧版拦掉。
    # 同时分歧本身不做二值拦截（收盘线检验显示 5.4 与 8.3 分歧的下注 ROI 无统计
    # 差异，闸门只减数量不提质量），改为标记风险等级供前端展示。
    divergence_gate = 6.0
    spread_divergence = (
        abs(model_mu_m - (-spread_line)) if spread_line is not None else 0.0
    )
    gated = spread_divergence > divergence_gate
    divergence_risk = "high" if spread_divergence > divergence_gate else ("medium" if spread_divergence > 3.0 else "low")

    if spread_line is not None:
        p_home = cover_prob(pricing_mu_m, spread_line, sigma_m)
        p_away = 1.0 - p_home
        probabilities["spread_home"] = p_home
        probabilities["spread_away"] = p_away
        if spread_cons and spread_cons.get("fair_home") is not None:
            fair_home, fair_away = spread_cons["fair_home"], spread_cons["fair_away"]
        else:
            pair = fair_two_way(
                home_spread["price"] if home_spread else None,
                away_spread["price"] if away_spread else None,
            )
            if not pair:
                fair_home = fair_away = None
            else:
                fair_home, fair_away = pair
        # 成交价取共识线下各侧最优价；book 记录该价格的真实来源
        home_price = (spread_cons or {}).get("home_price") or (home_spread["price"] if home_spread else None)
        away_price = (spread_cons or {}).get("away_price") or (away_spread["price"] if away_spread else None)
        home_book = (spread_cons or {}).get("home_book") or book or ""
        away_book = (spread_cons or {}).get("away_book") or book or ""
        for side, p, fair, price, side_line, price_book in (
            ("home", p_home, fair_home, home_price, spread_line, home_book),
            ("away", p_away, fair_away, away_price, -spread_line, away_book),
        ):
            if price is None or fair is None:
                continue
            signal = _make_edge("spread", side, side_line, price, p, fair, price_book, created_at)
            if signal and gated:
                signal["signal"] = False
                signal["gated"] = f"模型与市场分差分歧 {spread_divergence:.1f} >{divergence_gate:g}，仅记录不下注"
            if signal:
                edges.append(signal)

    if total_line is not None:
        p_over = over_prob(pricing_mu_t, total_line, sigma_t)
        p_under = 1.0 - p_over
        probabilities["total_over"] = p_over
        probabilities["total_under"] = p_under
        if total_cons and total_cons.get("fair_over") is not None:
            fair_over, fair_under = total_cons["fair_over"], total_cons["fair_under"]
        else:
            pair = fair_two_way(
                over_row["price"] if over_row else None,
                under_row["price"] if under_row else None,
            )
            if not pair:
                fair_over = fair_under = None
            else:
                fair_over, fair_under = pair
        over_price = (total_cons or {}).get("over_price") or (over_row["price"] if over_row else None)
        under_price = (total_cons or {}).get("under_price") or (under_row["price"] if under_row else None)
        over_book = (total_cons or {}).get("over_book") or book or ""
        under_book = (total_cons or {}).get("under_book") or book or ""
        for side, p, fair, price, price_book in (
            ("over", p_over, fair_over, over_price, over_book),
            ("under", p_under, fair_under, under_price, under_book),
        ):
            if price is None or fair is None:
                continue
            signal = _make_edge("total", side, total_line, price, p, fair, price_book, created_at)
            if signal:
                edges.append(signal)

    p_home_ml = moneyline_prob(pricing_mu_m, sigma_m)
    probabilities["moneyline_home"] = p_home_ml
    probabilities["moneyline_away"] = 1.0 - p_home_ml
    if home_ml or away_ml:
        ml_cons = cons.get("moneyline") or {}
        if ml_cons.get("fair_home") is not None:
            fair_home, fair_away = ml_cons["fair_home"], ml_cons["fair_away"]
        else:
            pair = fair_two_way(
                home_ml["price"] if home_ml else None,
                away_ml["price"] if away_ml else None,
            )
            if not pair:
                fair_home = fair_away = None
            else:
                fair_home, fair_away = pair
        home_price = ml_cons.get("home_price") or (home_ml["price"] if home_ml else None)
        away_price = ml_cons.get("away_price") or (away_ml["price"] if away_ml else None)
        home_book = ml_cons.get("home_book") or book or ""
        away_book = ml_cons.get("away_book") or book or ""
        # 陈旧最优价防护（审查第二轮 #2）：最优价来自明显超龄的书源（如 1xbet
        # 12h 未更新的 4.01）会虚造 edge——此时该侧降级用共识公允概率反推的
        # "可成交价上限"（fair 概率对应的无 vig 价格），保证 EV 不虚高。
        stale_flag = ml_cons.get("best_price_stale")
        if stale_flag and ml_cons.get("fair_home") and ml_cons.get("fair_away"):
            implied_home = round(1.0 / ml_cons["fair_home"], 3)
            implied_away = round(1.0 / ml_cons["fair_away"], 3)
            if home_price and home_price > implied_home:
                home_price = implied_home
            if away_price and away_price > implied_away:
                away_price = implied_away
        for side, p, fair, price, price_book in (
            ("home", p_home_ml, fair_home, home_price, home_book),
            ("away", 1.0 - p_home_ml, fair_away, away_price, away_book),
        ):
            if price is None or fair is None:
                continue
            # EV 用共识中位价（可确定拿到的价格）；最优价仅供展示比较
            ev_price = (ml_cons.get("home_price_median") if side == "home" else ml_cons.get("away_price_median")) or price
            signal = _make_edge("moneyline", side, None, price, p, fair, price_book, created_at, ev_price=ev_price)
            if signal and gated:
                signal["signal"] = False
                signal["gated"] = f"模型与市场分差分歧 {spread_divergence:.1f} >{divergence_gate:g}，仅记录不下注（跨市场保留观察）"
            if signal:
                edges.append(signal)

    edges.sort(key=lambda e: e["ev"], reverse=True)

    # --- 可下注标注（bettable）：signal 与 bettable 分离 ---
    # signal = 模型算出 EV；bettable = 价格真实可拿 + 共识深度够。
    # 闸门依据：历史信号 ROI −11%，最大 edge 分位 −27.7%——主死因是
    # "最优价其实是延迟线/限注单"。软书价、浅共识都降级为观察。
    for edge in edges:
        n_books = (
            (spread_cons or {}).get("n_books", 0)
            if edge["market"] == "spread"
            else (total_cons or {}).get("n_books", 0)
            if edge["market"] == "total"
            else (cons.get("moneyline") or {}).get("n_books", 0)
        )
        # 可下注必须是"信号"且未被分歧闸门拦下——观察项（EV 为负、被 gated）
        # 不能被标成可下注（旧版漏了这个前置条件，gated 的 ML 也会显示可下注）。
        if not edge.get("signal") or edge.get("gated"):
            edge["bettable"] = False
            if edge.get("gated"):
                edge["bettable_note"] = "分歧闸门已拦下（仅观察）"
            continue
        reason = _bettable_reason(edge, n_books, edge.get("book") or "")
        edge["bettable"] = reason is None
        if reason:
            edge["bettable_note"] = reason

    # --- 同场互斥：spread 与 moneyline 高度相关（大让分≈高胜率）---
    # 同场两个市场同时触发时只保留 EV 最高的一条可下注，另一条降为观察，
    # 防止相关性仓位叠加（组合层面的隐藏杠杆）。
    if MUTUAL_EXCLUSIVE:
        corr = [
            e for e in edges
            if e.get("signal") and e.get("bettable") and e["market"] in ("spread", "moneyline")
        ]
        if len(corr) > 1:
            keep = corr[0]  # edges 已按 EV 降序
            for e in corr[1:]:
                e["bettable"] = False
                e["bettable_note"] = f"同场与 {keep['market']}/{keep['side']} 相关（{e['market']} EV 更低），避免叠加仓位"

    return book, {
        "spread": spread_line,
        "total": total_line,
        "probabilities": probabilities,
        "edges": edges,
        "divergence_risk": divergence_risk,
        "consensus": {
            "n_books_spread": (spread_cons or {}).get("n_books", 0),
            "n_books_total": (total_cons or {}).get("n_books", 0),
            "n_books_ml": (cons.get("moneyline") or {}).get("n_books", 0),
            "stale": cons.get("stale", False),
            "sharp_anchor_spread": bool((spread_cons or {}).get("sharp_anchor")),
            "sharp_anchor_total": bool((total_cons or {}).get("sharp_anchor")),
            "sharp_anchor_ml": bool((cons.get("moneyline") or {}).get("sharp_anchor")),
        },
        "pricing": {
            "market_blend": MARKET_BLEND,
            "model_margin": model_mu_m,
            "model_total": model_mu_t,
            "market_margin": market_mu_m,
            "market_total": total_line,
            "margin": pricing_mu_m,
            "total": pricing_mu_t,
        },
    }


def predict_game(
    conn: sqlite3.Connection,
    game: dict,
    model: dict,
    elo: dict[int, float],
    rest: dict[int, int],
    density: dict[int, int] | None = None,
) -> dict:
    density = density or {}
    home_id, away_id = int(game["home_id"]), int(game["away_id"])
    injuries = conn.execute(
        "SELECT team_id, player, status, impact FROM injuries WHERE game_id=?",
        (game["id"],),
    ).fetchall()
    injury_home = sum(float(row["impact"]) for row in injuries if int(row["team_id"]) == home_id)
    injury_away = sum(float(row["impact"]) for row in injuries if int(row["team_id"]) == away_id)

    pred = ratings.predict(
        model,
        home_id,
        away_id,
        elo.get(home_id, 1500.0),
        elo.get(away_id, 1500.0),
        rest.get(home_id, 7),
        rest.get(away_id, 7),
        injury_home,
        injury_away,
        density.get(home_id, 0),
        density.get(away_id, 0),
    )
    odds_rows = latest_odds(conn, game["id"])
    book, market = build_market(pred, odds_rows, created_at=_now())

    try:
        injury_suggestions = rapm.suggest_impacts(conn, game["id"])
    except Exception:  # noqa: BLE001 - suggestions are best-effort
        injury_suggestions = []

    details = {
        "model": pred,
        "book": book,
        "lines": {"spread": market["spread"], "total": market["total"]},
        "probabilities": market["probabilities"],
        "pricing": market["pricing"],
        "edges": market["edges"],
        "consensus": market.get("consensus"),
        "divergence_risk": market.get("divergence_risk"),
        "injury_suggestions": injury_suggestions,
        "components": {
            "elo_home": elo.get(home_id, 1500.0),
            "elo_away": elo.get(away_id, 1500.0),
            "rest_home": rest.get(home_id, 7),
            "rest_away": rest.get(away_id, 7),
            "injury_home": injury_home,
            "injury_away": injury_away,
            "n_games": model["n_games"],
            "model_created_at": model["created_at"],
        },
    }
    cur = conn.execute(
        """
        INSERT INTO predictions(
            game_id, created_at, pred_margin, sigma_margin, pred_total, sigma_total, home_win_prob, details
        ) VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            game["id"],
            _now(),
            pred["pred_margin"],
            pred["sigma_margin"],
            pred["pred_total"],
            pred["sigma_total"],
            pred["home_win_prob"],
            json.dumps(details, ensure_ascii=False),
        ),
    )
    return {"prediction_id": int(cur.lastrowid), **pred, **details}


def predict_date(slate_date: str) -> list[dict]:
    conn = db.connect()
    try:
        model = ratings.latest_model(conn)
        if model is None:
            model = ratings.train_and_save(conn)
        placeholders = ",".join("?" for _ in SKIP_STATUSES)
        games = [
            dict(row)
            for row in conn.execute(
                f"""
                SELECT * FROM games
                WHERE slate_date=? AND status != 'STATUS_FINAL'
                  AND status NOT IN ({placeholders})
                ORDER BY COALESCE(tipoff, '')
                """,
                (slate_date, *SKIP_STATUSES),
            )
        ]
        elo = ratings.elo_as_of(conn, slate_date)
        team_ids = sorted({int(g["home_id"]) for g in games} | {int(g["away_id"]) for g in games})
        rest = ratings.rest_as_of(conn, slate_date, team_ids)
        density = ratings.density_as_of(conn, slate_date, team_ids)
        results = [predict_game(conn, game, model, elo, rest, density) for game in games]
        conn.commit()
        return results
    finally:
        conn.close()


def latest_prediction(conn: sqlite3.Connection, game_id: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM predictions WHERE game_id=? ORDER BY id DESC LIMIT 1",
        (game_id,),
    ).fetchone()
    if not row:
        return None
    result = dict(row)
    result["details"] = json.loads(result["details"])
    return result
