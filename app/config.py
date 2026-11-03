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
EDGE_THRESHOLD = float(os.getenv("NBAQ_EDGE", "0.03"))
MIN_EV = float(os.getenv("NBAQ_MIN_EV", "0.01"))
KELLY_FRACTION = float(os.getenv("NBAQ_KELLY", "0.25"))
MAX_STAKE_PCT = float(os.getenv("NBAQ_MAX_STAKE", "0.03"))

# How much weight to give the model vs the market line when pricing.
# 0.55 is deliberately conservative: single-book free lines are noisy, and
# early-season team ratings are stale. Set NBAQ_MARKET_BLEND=1.0 for pure model.
MARKET_BLEND = float(os.getenv("NBAQ_MARKET_BLEND", "0.55"))

# Uncertainty floors (points). Calibrate these with backtest data.
SIGMA_MARGIN_FLOOR = float(os.getenv("NBAQ_SIGMA_MARGIN", "10.5"))
SIGMA_TOTAL_FLOOR = float(os.getenv("NBAQ_SIGMA_TOTAL", "13.5"))

# Optional bearer token for the API. Empty = disabled (personal LAN use).
API_TOKEN = os.getenv("NBAQ_TOKEN", "")

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
USER_AGENT = "nba-quant-personal/0.1 (research)"
REQUEST_TIMEOUT = float(os.getenv("NBAQ_TIMEOUT", "25"))
REQUEST_SLEEP = float(os.getenv("NBAQ_SLEEP", "0.25"))

# Closing-line capture: refresh odds for games starting within this window.
CLOSING_WINDOW_MIN = float(os.getenv("NBAQ_CLOSING_WINDOW", "10"))

# Push notifications. Any provider left empty is disabled.
NTFY_SERVER = os.getenv("NBAQ_NTFY_SERVER", "https://ntfy.sh")
NTFY_TOPIC = os.getenv("NBAQ_NTFY_TOPIC", "")
TELEGRAM_BOT_TOKEN = os.getenv("NBAQ_TG_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("NBAQ_TG_CHAT_ID", "")

# Background watch loop
WATCH_INTERVAL = float(os.getenv("NBAQ_WATCH_INTERVAL", "300"))

# The Odds API（多书源共识，按量计费省着用）
THE_ODDS_API_KEY = os.getenv("NBAQ_ODDS_API_KEY", "")
# 两次拉取的最小间隔（小时）：默认 20h = 每天最多 1 次（3 markets × 1 region = 3 credits）
THE_ODDS_API_MIN_HOURS = float(os.getenv("NBAQ_ODDSAPI_MIN_HOURS", "20"))
