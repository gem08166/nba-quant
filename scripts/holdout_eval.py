"""冻结 holdout 验证：前 70% 场次拟合，后 30% 场次一次性考试。

与按月 walk-forward（backtest.py）互补：
- walk-forward 每月重拟合，模拟真实使用，但 σ 下限/MARKET_BLEND/regime 修正
  等超参是作者看着全部数据定的（轻微调参污染）；
- 本脚本把测试期完全冻结：模型参数、σ、Elo 终值都在 cutoff 前生成，
  cutoff 后只做纯预测。若 holdout 指标与 walk-forward 明显背离，
  说明超参过拟合了历史。

切分方式：按时间切（严禁随机切——随机会让测试期比赛通过 Elo/球队强度
泄漏进训练集）。默认 70/30，可用 --split 调整。

用法：python -m scripts.holdout_eval [--split 0.7] [--test-start 2026-08-01]
输出：data/holdout_report.json + 终端摘要
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone

import numpy as np

from app import db
from app.config import DATA_DIR
from app.models import ratings


def run(split: float = 0.7, min_games: int = 100) -> dict:
    conn = db.connect()
    try:
        finals = ratings.load_finals(conn)
        if len(finals) < min_games:
            return {"error": f"not enough finals: {len(finals)}"}
        finals = sorted(finals, key=lambda g: (g["slate_date"], g["id"]))

        cut = int(len(finals) * split)
        cutoff = finals[cut]["slate_date"]
        # 测试期从完整比赛日开始切（同日比赛不拆）
        while cut < len(finals) and finals[cut]["slate_date"] == cutoff:
            cut += 1
        train, test = finals[:cut], finals[cut:]
        cutoff = test[0]["slate_date"]

        # --- 冻结训练期的一切 ---
        model = ratings.fit(conn, as_of=cutoff)
        # σ 用训练期残差样本外放大（与生产同源超参）
        pregame_elo_train, elo_final = ratings.compute_elo(train)
        rest_train = ratings.compute_rest(train)
        train_errs_m, train_errs_t = [], []
        for g in train:
            p = ratings.predict(
                model, int(g["home_id"]), int(g["away_id"]),
                pregame_elo_train[g["id"]][0], pregame_elo_train[g["id"]][1],
                rest_train[g["id"]][0], rest_train[g["id"]][1],
            )
            train_errs_m.append(p["pred_margin"] - (g["home_score"] - g["away_score"]))
            train_errs_t.append(p["pred_total"] - (g["home_score"] + g["away_score"]))
        sigma_m_train = float(np.std(train_errs_m))
        sigma_t_train = float(np.std(train_errs_t))

        # --- 测试期：纯预测，零训练信息 ---
        rest_full = ratings.compute_rest(train)
        rows: list[dict] = []
        for g in test:
            elo_h, elo_a = elo_final.get(int(g["home_id"]), 1500.0), elo_final.get(int(g["away_id"]), 1500.0)
            r_h, r_a = rest_full.get(int(g["home_id"]), 7), rest_full.get(int(g["away_id"]), 7)
            p = ratings.predict(model, int(g["home_id"]), int(g["away_id"]), elo_h, elo_a, r_h, r_a)
            margin = g["home_score"] - g["away_score"]
            total = g["home_score"] + g["away_score"]
            home_win = 1.0 if margin > 0 else 0.0
            rows.append({
                "date": g["slate_date"],
                "pred_margin": p["pred_margin"],
                "actual_margin": margin,
                "pred_total": p["pred_total"],
                "actual_total": total,
                "home_win_prob": p["home_win_prob"],
                "home_win": home_win,
                # 基线
                "train_mean_margin": float(np.mean([x["home_score"] - x["away_score"] for x in train])),
                "train_mean_total": float(np.mean([x["home_score"] + x["away_score"] for x in train])),
            })

        def _block(rs: list[dict]) -> dict:
            if not rs:
                return {"n": 0}
            me = np.array([r["pred_margin"] - r["actual_margin"] for r in rs])
            te = np.array([r["pred_total"] - r["actual_total"] for r in rs])
            hw = np.array([r["home_win"] for r in rs])
            hp = np.array([r["home_win_prob"] for r in rs])
            brier = float(np.mean((hp - hw) ** 2))
            # 概率校准 ECE（10 桶）
            ece = 0.0
            for b in range(10):
                lo, hi = b / 10, (b + 1) / 10
                mask = (hp >= lo) & (hp < hi if b < 9 else hp <= hi)
                if mask.sum() >= 5:
                    ece += mask.sum() / len(hp) * abs(hw[mask].mean() - hp[mask].mean())
            return {
                "n": len(rs),
                "margin_mae": round(float(np.mean(np.abs(me))), 3),
                "margin_rmse": round(float(np.sqrt(np.mean(me ** 2))), 3),
                "margin_bias": round(float(np.mean(me)), 3),
                "total_mae": round(float(np.mean(np.abs(te))), 3),
                "total_bias": round(float(np.mean(te)), 3),
                "brier": round(brier, 4),
                "ece": round(ece, 4),
                "home_win_accuracy": round(float(np.mean((hp >= 0.5) == (hw == 1))), 4),
            }

        # 基线：训练期均值常数预测
        const_m = float(np.mean([g["home_score"] - g["away_score"] for g in train]))
        const_t = float(np.mean([g["home_score"] + g["away_score"] for g in train]))
        base_margin_mae = float(np.mean([abs(const_m - (g["home_score"] - g["away_score"])) for g in test]))
        base_total_mae = float(np.mean([abs(const_t - (g["home_score"] + g["away_score"])) for g in test]))
        base_brier = float(np.mean([(0.5 - (1.0 if g["home_score"] - g["away_score"] > 0 else 0.0)) ** 2 for g in test]))

        report = {
            "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "split": split,
            "train_games": len(train),
            "test_games": len(test),
            "train_period": [train[0]["slate_date"], train[-1]["slate_date"]],
            "test_period": [test[0]["slate_date"], test[-1]["slate_date"]],
            "cutoff_date": cutoff,
            "train_sigma_margin": round(sigma_m_train, 3),
            "train_sigma_total": round(sigma_t_train, 3),
            "model_sigma_margin": round(model["sigma_margin"], 3),
            "model_sigma_total": round(model["sigma_total"], 3),
            "test": _block(rows),
            "baseline": {
                "constant_margin_mae": round(base_margin_mae, 3),
                "constant_total_mae": round(base_total_mae, 3),
                "constant_brier": round(base_brier, 4),
                "constant_margin": round(const_m, 2),
                "constant_total": round(const_t, 2),
            },
        }
        path = DATA_DIR / "holdout_report.json"
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(report, handle, ensure_ascii=False, indent=2)
        return report
    finally:
        conn.close()


def print_report(report: dict) -> None:
    if report.get("error"):
        print(report["error"])
        return
    t = report["test"]
    b = report["baseline"]
    print(
        f"冻结 holdout：训练 {report['train_games']} 场（{report['train_period'][0]} ~ {report['train_period'][1]}）"
        f" → 测试 {report['test_games']} 场（{report['test_period'][0]} ~ {report['test_period'][1]}）"
    )
    print(
        f"σ 一致性：训练期残差 ({report['train_sigma_margin']}, {report['train_sigma_total']})"
        f" vs 模型存储 ({report['model_sigma_margin']}, {report['model_sigma_total']})"
    )
    print(
        f"测试期：分差 MAE {t['margin_mae']}（常数基线 {b['constant_margin_mae']}）· "
        f"总分 MAE {t['total_mae']}（常数基线 {b['constant_total_mae']}）"
    )
    print(f"        Brier {t['brier']}（50/50 基线 {b['constant_brier']}）· ECE {t['ece']} · 方向 {t['home_win_accuracy']:.1%}")
    print(f"        bias：分差 {t['margin_bias']:+.2f} · 总分 {t['total_bias']:+.2f}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--split", type=float, default=0.7, help="训练占比（默认 0.7）")
    args = parser.parse_args()
    rep = run(split=args.split)
    print_report(rep)
