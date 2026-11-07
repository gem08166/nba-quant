from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from dotenv import load_dotenv  # noqa: E402

# 支持 .env 文件（gitignore 内，存放 API key 等敏感配置）
load_dotenv(ROOT / ".env")
DATA_DIR = Path(os.getenv("NBAQ_DATA_DIR", ROOT / "data"))
DB_PATH = Path(os.getenv("NBAQ_DB", DATA_DIR / "nba.db"))
RAW_DIR = DATA_DIR / "raw"
WEB_DIR = Path(__file__).resolve().parent / "web"

# Decision thresholds
EDGE_THRESHOLD = float(os.getenv("NBAQ_EDGE", "0.05"))
MIN_EV = float(os.getenv("NBAQ_MIN_EV", "0.01"))
KELLY_FRACTION = float(os.getenv("NBAQ_KELLY", "0.25"))
MAX_STAKE_PCT = float(os.getenv("NBAQ_MAX_STAKE", "0.03"))

# 共识新鲜度：某市场新鲜书源少于该数量时，回退到全量书源并标记 stale。
# 只用 1-2 家新鲜书源会让共识退化成单书，比"略旧的 12 家共识"更差。
MIN_FRESH_BOOKS = int(os.getenv("NBAQ_MIN_FRESH_BOOKS", "3"))

# 信号有效期：超过该小时数未更新（或比赛已开赛/结束）即标记过期，
# 不再计入「信号/可下注」统计，也不出现在可下注面板。
SIGNAL_MAX_AGE_HOURS = float(os.getenv("NBAQ_SIGNAL_MAX_AGE_HOURS", "12"))

# Sharp 锚定共识（散户真实 edge 来源的转变：模型 vs 全体共识 → 软书 vs sharp 书）。
# 开启后：若共识里有 sharp 书源（如 Pinnacle），fair 概率取 sharp 书去水值，
# 软书价格只用来回答"哪里能拿到价"；signals 额外输出 sharp_ev（按 sharp 公允
# 概率对最优软书价的纯价差 EV，不依赖模型）。无 sharp 书源时自动回退全体共识。
SHARP_ANCHOR = os.getenv("NBAQ_SHARP_ANCHOR", "0") == "1"

# 同场互斥：让分与胜负高度相关（大让分 ≈ 高胜率），同场两者同时触发信号时
# 只保留 EV 最高的一条为可下注，另一条降级为观察（防止相关性叠加仓位）。
MUTUAL_EXCLUSIVE = os.getenv("NBAQ_MUTUAL_EXCLUSIVE", "1") == "1"

# 信号可下注（bettable）标注的共识深度要求：低于此书源数的信号只算观察。
BETTABLE_MIN_BOOKS = int(os.getenv("NBAQ_BETTABLE_MIN_BOOKS", "3"))
# 最优价相对共识中位价的离群容忍（超过视为"拿不到的价"，只观察不下注）。
BETTABLE_PRICE_OUTLIER = float(os.getenv("NBAQ_BETTABLE_PRICE_OUTLIER", "0.08"))

# How much weight to give the model vs the market line when pricing.
# 0.55 is deliberately conservative: single-book free lines are noisy, and
# early-season team ratings are stale. Set NBAQ_MARKET_BLEND=1.0 for pure model.
MARKET_BLEND = float(os.getenv("NBAQ_MARKET_BLEND", "0.55"))

# Uncertainty floors (points). Calibrate these with backtest data.
SIGMA_MARGIN_FLOOR = float(os.getenv("NBAQ_SIGMA_MARGIN", "10.5"))
SIGMA_TOTAL_FLOOR = float(os.getenv("NBAQ_SIGMA_TOTAL", "13.5"))
# σ 样本外缩放：样本内残差系统性低估样本外误差（实测 margin 14.51 vs RMSE 15.21），
# 用 walk-forward RMSE 比值放大，消除定价过度自信（第三方审查 P1-4）。
SIGMA_OOS_SCALE_MARGIN = float(os.getenv("NBAQ_SIGMA_SCALE_MARGIN", "1.048"))
SIGMA_OOS_SCALE_TOTAL = float(os.getenv("NBAQ_SIGMA_SCALE_TOTAL", "1.014"))

# Optional bearer token for the API. Empty = disabled (personal LAN use).
API_TOKEN = os.getenv("NBAQ_TOKEN", "")

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
USER_AGENT = "nba-quant-personal/0.1 (research)"
REQUEST_TIMEOUT = float(os.getenv("NBAQ_TIMEOUT", "25"))
REQUEST_SLEEP = float(os.getenv("NBAQ_SLEEP", "0.25"))

# Closing-line capture: refresh odds for games starting within this window.
# 120 分钟：收盘前能抓到多个时点的快照（线移动检验的样本来源）
CLOSING_WINDOW_MIN = float(os.getenv("NBAQ_CLOSING_WINDOW", "120"))

# Push notifications. Any provider left empty is disabled.
NTFY_SERVER = os.getenv("NBAQ_NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.getenv("NBAQ_NTFY_TOPIC", "")
TELEGRAM_BOT_TOKEN = os.getenv("NBAQ_TG_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("NBAQ_TG_CHAT_ID", "")

# Background watch loop
WATCH_INTERVAL = float(os.getenv("NBAQ_WATCH_INTERVAL", "300"))
# 有比赛进行中时的采样间隔：赛中价格/胜率变化快，15 分钟一个点不够做校准
LIVE_INTERVAL = float(os.getenv("NBAQ_WATCH_LIVE_INTERVAL", "120"))
# 本机把「滚球盘口」小文件推到坚果云（手机快速通道，不碰台账/静态站）
LOCAL_PUBLISH_LIVE = os.getenv("NBAQ_LOCAL_PUBLISH_LIVE", "0") == "1"
# 赛时每隔多少分钟主动 workflow_dispatch 一轮 CI（0=关闭）。
# GitHub 对 schedule 限流到约 1 小时，dispatch 不受限；只在本机开着时生效。
CI_TRIGGER_MINUTES = float(os.getenv("NBAQ_CI_TRIGGER_MINUTES", "0"))

# The Odds API（多书源共识，按量计费省着用）
THE_ODDS_API_KEY = os.getenv("NBAQ_ODDS_API_KEY", "")
# 两次拉取的最小间隔（小时）：默认 20h = 每天最多 1 次（3 markets × 1 region = 3 credits）
THE_ODDS_API_MIN_HOURS = float(os.getenv("NBAQ_ODDSAPI_MIN_HOURS", "20"))

# HoopsAPI（多书盘口 + 完赛收盘归档，含 Pinnacle）
HOOPS_API_KEY = os.getenv("HOOPS_API_KEY", "")

# 坚果云 WebDAV 台账同步（PC 端服务端直连，无 CORS 问题）。
# NBAQ_WEBDAV_USER = 坚果云注册邮箱；NBAQ_WEBDAV_PASS = 应用密码
# （坚果云网页 → 账户信息 → 安全选项 → 第三方应用管理 → 添加应用密码）。
# 留空 = 不同步。
WEBDAV_URL = os.getenv("NBAQ_WEBDAV_URL", "https://dav.jianguoyun.com/dav/")
WEBDAV_USER = os.getenv("NBAQ_WEBDAV_USER", "")
WEBDAV_PASS = os.getenv("NBAQ_WEBDAV_PASS", "")
