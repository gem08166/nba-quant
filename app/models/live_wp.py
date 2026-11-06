"""赛中胜率模型（in-game win probability）——研究模块，不产生下注信号。

模型形式
--------
把"当前分差"沿剩余时间投射到终场，再用正态分布给出胜率：

    μ(t) = margin_now + w · μ_pregame · (t / T)
    σ(t) = sqrt(σ_full² · (t / T) + σ_floor²)
    P(主胜) = Φ( μ(t) / σ(t) )

- t：有效剩余秒数（第 4 节末为 0；加时按当前加时的剩余时钟算）
- T：常规时间 2880 秒
- w：赛前预期带入权重（0 = 完全忽略赛前，1 = 完整带入）
- σ_full：全场分差标准差（典型 NBA 约 11–13）
- σ_floor：终场附近的不确定性下限（压哨球仍可能改变结果）

三个参数用历史逐回合数据标定（见 scripts/live_wp_fit.py），
目标是最小化 logloss（等价于校准良好的概率）。

**这里只做记录与研究**：不输出 edge、不进入信号/可下注通道。
"""
from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime, timezone

from ..config import DATA_DIR
from ..pricing.devig import norm_cdf

T_REGULATION = 2880.0  # 4 × 720
PERIOD = 720.0
OT = 300.0

PARAMS_PATH = DATA_DIR / "live_wp_params.json"
REPORT_PATH = DATA_DIR / "live_wp_report.json"

DEFAULT_PARAMS = {
    # 与 scripts/live_wp_fit.py 的标定结果一致（参数文件缺失时也保持一致行为）
    "prior_weight": 1.0,
    "sigma_full": 24.0,
    "gamma": 0.6,
    "sigma_floor": 1.6,
}


def load_params() -> dict:
    try:
        payload = json.loads(PARAMS_PATH.read_text(encoding="utf-8"))
        return {
            "prior_weight": float(payload["prior_weight"]),
            "sigma_full": float(payload["sigma_full"]),
            "gamma": float(payload.get("gamma", 0.5)),
            "sigma_floor": float(payload["sigma_floor"]),
        }
    except (OSError, KeyError, TypeError, ValueError):
        return dict(DEFAULT_PARAMS)


def load_report() -> dict | None:
    try:
        return json.loads(REPORT_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def seconds_remaining(period: int | None, clock_seconds: float | None) -> float:
    """有效剩余秒数。

    第 1–4 节：常规时间剩余（第 4 节结束为 0）。
    加时：只算当前加时还剩多少秒（加时是 5 分钟的迷你比赛）。
    """
    period = period or 0
    clock = max(clock_seconds or 0.0, 0.0)
    if period <= 0:
        return T_REGULATION
    if period <= 4:
        played = (period - 1) * PERIOD + (PERIOD - clock)
        return max(T_REGULATION - played, 0.0)
    return min(clock, OT)


def win_prob(
    margin: float,
    t_remaining: float,
    prior_margin: float = 0.0,
    params: dict | None = None,
) -> float:
    """P(主队获胜 | 当前分差、剩余时间、赛前预期)。

    σ(t) = sqrt(σ_full² · (t/T)^(2γ) + σ_floor²)
    """
    p = params or DEFAULT_PARAMS
    frac = max(t_remaining, 0.0) / T_REGULATION
    mu = margin + p["prior_weight"] * prior_margin * frac
    gamma = p.get("gamma", 0.5)
    sigma = math.sqrt(p["sigma_full"] ** 2 * frac ** (2 * gamma) + p["sigma_floor"] ** 2)
    return norm_cdf(mu / sigma)


# ---------------------------------------------------------------------------
# 记录：把进行中比赛的胜率轨迹存下来（供未来与滚球盘口做校准对照）
# ---------------------------------------------------------------------------


def record_snapshot(conn: sqlite3.Connection, slate: str, client=None) -> dict:
    """抓一次 ESPN 记分板，给进行中的比赛写入一行胜率快照。

    每 15 分钟的 watch tick 调用一次即可（一个 HTTP 请求）。
    同一 (game_id, period, clock) 只存一次，避免重复行。
    """
    import httpx

    from ..config import ESPN_BASE, REQUEST_TIMEOUT, USER_AGENT
    from ..jobs.predict import latest_prediction
    from ..jobs.live import _parse_event

    params = load_params()
    written = 0
    seen = 0
    try:
        if client is None:
            with httpx.Client(
                headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
                follow_redirects=True,
            ) as c:
                resp = c.get(
                    f"{ESPN_BASE}/scoreboard", params={"dates": slate.replace("-", "")}
                )
                resp.raise_for_status()
                payload = resp.json()
        else:
            resp = client.get(
                f"{ESPN_BASE}/scoreboard", params={"dates": slate.replace("-", "")}
            )
            resp.raise_for_status()
            payload = resp.json()
    except Exception as exc:  # noqa: BLE001 - 记录失败不影响主流程
        return {"written": 0, "error": str(exc)}

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    for event in payload.get("events") or []:
        parsed = _parse_event(event)
        if not parsed or parsed.get("state") != "in":
            continue
        seen += 1
        game_id = parsed["game_id"]
        margin = parsed["home_score"] - parsed["away_score"]
        t_rem = seconds_remaining(parsed.get("period"), parsed.get("clock_seconds"))
        pred = latest_prediction(conn, game_id)
        prior = float(pred["pred_margin"]) if pred else 0.0
        prob = win_prob(margin, t_rem, prior, params)
        clock_key = parsed.get("display_clock") or f"{parsed.get('clock_seconds')}"
        conn.execute(
            """
            INSERT OR IGNORE INTO live_wp
                (game_id, period, clock, home_score, away_score, margin,
                 seconds_remaining, prior_margin, home_win_prob, captured_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                game_id,
                parsed.get("period"),
                clock_key,
                parsed.get("home_score"),
                parsed.get("away_score"),
                margin,
                round(t_rem, 1),
                round(prior, 2),
                round(prob, 5),
                now,
            ),
        )
        written += 1
    conn.commit()
    return {"written": written, "in_play": seen}
