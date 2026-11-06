"""标定赛中胜率模型参数（研究模块，不产生下注信号）。

模型：
    μ(t) = margin_now + w · μ_pregame · (t/T)
    σ(t) = sqrt(σ_full² · (t/T)^(2γ) + σ_floor²)
    P(主胜) = Φ( μ(t) / σ(t) )

四个参数（w, σ_full, γ, σ_floor）用 pbp_plays 的历史逐回合数据拟合，
目标是最小化 logloss。**按比赛日做时间切分**：前 70% 比赛训练、
后 30% 比赛做一次性样本外验收（与项目其他模块的 holdout 口径一致）。

输出：
  data/live_wp_params.json   生产参数（全样本拟合）
  data/live_wp_report.json   验收报告（训练/样本外指标 + 可靠性表 + 分节表现）

用法：python -m scripts.live_wp_fit
"""
from __future__ import annotations

import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import db
from app.config import DATA_DIR
from app.models.live_wp import PARAMS_PATH, REPORT_PATH, T_REGULATION, seconds_remaining

# 参数网格（4 个参数，样本量 6 万，网格足够稳定）
# 赛前预期带入权重固定为 1（完整带入）：只有约 11% 的回合能取到赛前预测，
# w 在网格里不稳定（训练集选出 0.0、全样本选出 1.0）；固定为 1 理论上更合理
# （与 live.py 的投射口径一致），也避免小样本过拟合。
W_GRID = (1.0,)
SIGMA_FULL_GRID = np.arange(8.0, 34.01, 1.0)
GAMMA_GRID = (0.35, 0.45, 0.5, 0.6, 0.7, 0.85)
SIGMA_FLOOR_GRID = (0.8, 1.2, 1.6, 2.0, 2.5)


def parse_clock(text: str | None) -> float | None:
    """'11:40' → 700.0 秒。"""
    if not text:
        return None
    try:
        parts = str(text).strip().split(":")
        if len(parts) == 2:
            return float(parts[0]) * 60 + float(parts[1])
        return float(parts[0])
    except (TypeError, ValueError):
        return None


def load_samples() -> dict:
    conn = db.connect()
    try:
        games = {
            r["id"]: (r["home_score"], r["away_score"], r["slate_date"])
            for r in conn.execute(
                "SELECT id, home_score, away_score, slate_date FROM games "
                "WHERE home_score IS NOT NULL AND away_score IS NOT NULL"
            )
        }
        priors = {
            r["game_id"]: r["pred_margin"]
            for r in conn.execute(
                """
                SELECT p.game_id, p.pred_margin FROM predictions p
                JOIN (SELECT game_id, MAX(id) mid FROM predictions GROUP BY game_id) m
                  ON m.mid = p.id
                """
            )
        }
        rows = conn.execute(
            "SELECT game_id, period, clock, home_score, away_score FROM pbp_plays "
            "WHERE home_score IS NOT NULL AND away_score IS NOT NULL"
        ).fetchall()
    finally:
        conn.close()

    margin, t_rem, prior, y, period, gid, date = [], [], [], [], [], [], []
    for r in rows:
        final = games.get(r["game_id"])
        if not final or final[0] == final[1]:
            continue
        clock = parse_clock(r["clock"])
        if clock is None:
            continue
        margin.append(r["home_score"] - r["away_score"])
        t_rem.append(seconds_remaining(r["period"], clock))
        prior.append(float(priors.get(r["game_id"]) or 0.0))
        y.append(1.0 if final[0] > final[1] else 0.0)
        period.append(r["period"] or 0)
        gid.append(r["game_id"])
        date.append(final[2])

    return {
        "margin": np.asarray(margin, float),
        "t_rem": np.asarray(t_rem, float),
        "prior": np.asarray(prior, float),
        "y": np.asarray(y, float),
        "period": np.asarray(period, int),
        "game": np.asarray(gid),
        "date": np.asarray(date),
    }


def norm_cdf_arr(x: np.ndarray) -> np.ndarray:
    """向量化 Φ（与 app.pricing.devig.norm_cdf 同式）。"""
    return 0.5 * (1.0 + np.vectorize(math.erf)(x / math.sqrt(2.0)))


def predict_p(margin, t_rem, prior, w, s_full, gamma, s_floor) -> np.ndarray:
    frac = np.maximum(t_rem, 0.0) / T_REGULATION
    mu = margin + w * prior * frac
    sigma = np.sqrt(s_full**2 * frac ** (2 * gamma) + s_floor**2)
    return norm_cdf_arr(mu / sigma)


def logloss(p: np.ndarray, y: np.ndarray) -> float:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return float(-np.mean(y * np.log(p) + (1 - y) * np.log(1 - p)))


def brier(p: np.ndarray, y: np.ndarray) -> float:
    return float(np.mean((p - y) ** 2))


def fit(sample: dict) -> tuple:
    best = None
    m, t, pr, y = sample["margin"], sample["t_rem"], sample["prior"], sample["y"]
    for w in W_GRID:
        for s_full in SIGMA_FULL_GRID:
            for gamma in GAMMA_GRID:
                for s_floor in SIGMA_FLOOR_GRID:
                    p = predict_p(m, t, pr, w, s_full, gamma, s_floor)
                    ll = logloss(p, y)
                    if best is None or ll < best[0]:
                        best = (ll, float(w), float(s_full), float(gamma), float(s_floor))
    return best[1:]


def calibration_table(p: np.ndarray, y: np.ndarray, bins: int = 10) -> list[dict]:
    out = []
    edges = np.linspace(0.0, 1.0, bins + 1)
    for i in range(bins):
        lo, hi = edges[i], edges[i + 1]
        mask = (p >= lo) & (p < hi if i < bins - 1 else p <= hi)
        n = int(mask.sum())
        if not n:
            continue
        out.append(
            {
                "bin": f"{lo:.1f}-{hi:.1f}",
                "n": n,
                "pred": round(float(p[mask].mean()), 4),
                "actual": round(float(y[mask].mean()), 4),
                "gap": round(float(y[mask].mean() - p[mask].mean()), 4),
            }
        )
    return out


def metrics(p: np.ndarray, y: np.ndarray) -> dict:
    return {
        "n": int(len(y)),
        "logloss": round(logloss(p, y), 5),
        "brier": round(brier(p, y), 5),
        "baseline_logloss_0.5": round(logloss(np.full_like(p, 0.5), y), 5),
    }


def slice_sample(s: dict, mask: np.ndarray) -> dict:
    return {k: v[mask] for k, v in s.items()}


def main() -> None:
    s = load_samples()
    n = len(s["y"])
    if n == 0:
        print("没有可用样本（需要 pbp_plays + 最终比分）", file=sys.stderr)
        raise SystemExit(1)

    games = sorted(set(s["date"].tolist()))
    cut = games[max(0, int(len(games) * 0.7) - 1)] if len(games) > 1 else games[0]
    train_mask = s["date"] <= cut
    test_mask = ~train_mask

    print(
        f"samples: {n} plays / {len(set(s['game'].tolist()))} games / {len(games)} dates"
    )
    print(f"split at {cut}: train {int(train_mask.sum())} plays, test {int(test_mask.sum())} plays")

    train = slice_sample(s, train_mask)
    w, s_full, gamma, s_floor = fit(train)
    print(f"fit(train): w={w} sigma_full={s_full} gamma={gamma} sigma_floor={s_floor}")

    # 样本外验收
    holdout = None
    if int(test_mask.sum()) > 100:
        test = slice_sample(s, test_mask)
        p_test = predict_p(test["margin"], test["t_rem"], test["prior"], w, s_full, gamma, s_floor)
        holdout = {
            "split_date": cut,
            "params": {
                "prior_weight": w,
                "sigma_full": s_full,
                "gamma": gamma,
                "sigma_floor": s_floor,
            },
            "metrics": metrics(p_test, test["y"]),
            "calibration": calibration_table(p_test, test["y"]),
        }
        print(
            f"holdout: logloss {holdout['metrics']['logloss']:.5f} "
            f"(baseline {holdout['metrics']['baseline_logloss_0.5']:.5f}) "
            f"brier {holdout['metrics']['brier']:.5f}"
        )

    # 生产参数：全样本重新拟合
    w_a, s_full_a, gamma_a, s_floor_a = fit(s)
    p_all = predict_p(s["margin"], s["t_rem"], s["prior"], w_a, s_full_a, gamma_a, s_floor_a)
    print(f"fit(all): w={w_a} sigma_full={s_full_a} gamma={gamma_a} sigma_floor={s_floor_a}")

    by_period = []
    for per in sorted(set(s["period"].tolist())):
        mask = s["period"] == per
        if int(mask.sum()) < 50:
            continue
        by_period.append(
            {
                "period": int(per),
                "n": int(mask.sum()),
                "brier": round(brier(p_all[mask], s["y"][mask]), 5),
                "logloss": round(logloss(p_all[mask], s["y"][mask]), 5),
            }
        )

    late_mask = s["t_rem"] <= 120
    late = (
        {
            "n": int(late_mask.sum()),
            "brier": round(brier(p_all[late_mask], s["y"][late_mask]), 5),
            "logloss": round(logloss(p_all[late_mask], s["y"][late_mask]), 5),
        }
        if int(late_mask.sum()) > 0
        else None
    )

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "kind": "in-game win probability (research only, no betting signals)",
        "params": {
            "prior_weight": w_a,
            "sigma_full": s_full_a,
            "gamma": gamma_a,
            "sigma_floor": s_floor_a,
        },
        "sample": {
            "plays": n,
            "games": len(set(s["game"].tolist())),
            "dates": len(games),
            "plays_with_prior": int((s["prior"] != 0).sum()),
            # 逐回合数据高度相关（同一场内的回合不是独立样本），
            # 有效样本量接近"场次"而不是"回合数"，校准表的置信区间因此很宽。
            "effective_n_note": "逐回合高度相关，有效样本接近场次数（当前 %d 场），校准仅作方向性参考"
            % len(set(s["game"].tolist())),
        },
        "caveat": (
            "当前只有 %d 场比赛的逐回合数据，回合之间高度相关，"
            "有效样本接近场次数；分箱校准的波动很大，只能作方向性参考。"
            "需要累积 30 天以上比赛再评估是否可用于任何决策。" % len(set(s["game"].tolist()))
        ),
        "in_sample": metrics(p_all, s["y"]),
        "holdout": holdout,
        "by_period": by_period,
        "late_2min": late,
        "calibration": calibration_table(p_all, s["y"]),
    }

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    PARAMS_PATH.write_text(
        json.dumps(
            {
                "prior_weight": w_a,
                "sigma_full": s_full_a,
                "gamma": gamma_a,
                "sigma_floor": s_floor_a,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    REPORT_PATH.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"  in-sample logloss {report['in_sample']['logloss']:.5f} brier {report['in_sample']['brier']:.5f}")
    print("  calibration (pred vs actual):")
    for row in report["calibration"]:
        print(
            f"    {row['bin']:>9} n={row['n']:<7} pred={row['pred']:.3f} "
            f"actual={row['actual']:.3f} gap={row['gap']:+.3f}"
        )
    print(f"wrote {PARAMS_PATH}")
    print(f"wrote {REPORT_PATH}")


if __name__ == "__main__":
    main()
