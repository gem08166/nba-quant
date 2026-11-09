from pathlib import Path

# ============ 1. config.py：支持多 key 轮换 + 频率提升 ============
p = Path("app/config.py")
text = p.read_text(encoding="utf-8")

old = '''# The Odds API（多书源共识，按量计费省着用）
THE_ODDS_API_KEY = os.getenv("NBAQ_ODDS_API_KEY", "")
# 两次拉取的最小间隔（小时）：默认 20h = 每天最多 1 次（3 markets × 1 region = 3 credits）
THE_ODDS_API_MIN_HOURS = float(os.getenv("NBAQ_ODDSAPI_MIN_HOURS", "20"))'''
new = '''# The Odds API（多书源共识）。支持多 key 轮换：逗号分隔，按序消耗，
# 某个 key 429/额度尽自动切下一个（fetch_nba_odds 内处理）。
_raw_keys = os.getenv("NBAQ_ODDS_API_KEY", "")
THE_ODDS_API_KEYS = [k.strip() for k in _raw_keys.split(",") if k.strip()]
THE_ODDS_API_KEY = THE_ODDS_API_KEYS[0] if THE_ODDS_API_KEYS else ""
# 两次拉取的最小间隔（小时）：默认 4h（多 key 轮换后额度充足，
# 3 markets × 1 region = 3 credits/次；4h = 6 次/天/key ≈ 每月 540/Key）
THE_ODDS_API_MIN_HOURS = float(os.getenv("NBAQ_ODDSAPI_MIN_HOURS", "4"))'''
assert old in text, "config block not found"
text = text.replace(old, new, 1)
p.write_text(text, encoding="utf-8")
print("config.py done")
