from pathlib import Path

p = Path("app/ingest/theodds.py")
text = p.read_text(encoding="utf-8")

# 1) import 换成多 key 列表
old_imp = "from ..config import DATA_DIR, REQUEST_TIMEOUT, THE_ODDS_API_KEY, THE_ODDS_API_MIN_HOURS"
new_imp = "from ..config import DATA_DIR, REQUEST_TIMEOUT, THE_ODDS_API_KEYS, THE_ODDS_API_MIN_HOURS"
assert old_imp in text
text = text.replace(old_imp, new_imp, 1)

# 2) key 检查行
old_key = '''    if not THE_ODDS_API_KEY:
        return [], "未配置 NBAQ_ODDS_API_KEY，跳过"'''
new_key = '''    if not THE_ODDS_API_KEYS:
        return [], "未配置 NBAQ_ODDS_API_KEY，跳过"'''
assert old_key in text
text = text.replace(old_key, new_key, 1)

# 3) 请求段：轮换 key——从上次使用的 key 之后继续（state 记 key_idx），
#    429/额度尽自动切下一个 key 重试一次
old_req = '''    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        resp = client.get(
            f"{API_BASE}/sports/{SPORT_NBA}/odds/",
            params={
                "apiKey": THE_ODDS_API_KEY,
                "regions": REGION,
                "markets": MARKETS,
                "oddsFormat": "decimal",
            },
        )
        remaining = resp.headers.get("x-requests-remaining")
        used = resp.headers.get("x-requests-used")
        if resp.status_code == 429:
            _save_state({**_load_state(), "last_fetch_ts": time.time()})
            return [], f"额度用尽（429），remaining={remaining}"
        resp.raise_for_status()
        events = resp.json()'''
new_req = '''    state = _load_state()
    key_idx = int(state.get("key_idx") or 0) % len(THE_ODDS_API_KEYS)
    events = None
    with httpx.Client(timeout=REQUEST_TIMEOUT) as client:
        for attempt in range(len(THE_ODDS_API_KEYS)):
            key = THE_ODDS_API_KEYS[(key_idx + attempt) % len(THE_ODDS_API_KEYS)]
            resp = client.get(
                f"{API_BASE}/sports/{SPORT_NBA}/odds/",
                params={
                    "apiKey": key,
                    "regions": REGION,
                    "markets": MARKETS,
                    "oddsFormat": "decimal",
                },
            )
            remaining = resp.headers.get("x-requests-remaining")
            used = resp.headers.get("x-requests-used")
            if resp.status_code == 429 and attempt < len(THE_ODDS_API_KEYS) - 1:
                # 当前 key 额度尽 → 记录并轮换到下一个 key 重试
                _save_state({**_load_state(), "key_idx": (key_idx + attempt + 1) % len(THE_ODDS_API_KEYS)})
                continue
            if resp.status_code == 429:
                _save_state({**_load_state(), "last_fetch_ts": time.time()})
                return [], f"全部 key 额度用尽（429），remaining={remaining}"
            resp.raise_for_status()
            events = resp.json()
            key_idx = (key_idx + attempt) % len(THE_ODDS_API_KEYS)
            _save_state({**_load_state(), "key_idx": key_idx})
            break
    if events is None:
        return [], "未获取到盘口数据"'''
assert old_req in text
text = text.replace(old_req, new_req, 1)

# 4) 原来的 state = _load_state()（fetch 后那段）改为只更新
old_state = '''    state = _load_state()
    state.update('''
new_state = '''    state.update('''
# 只替换紧跟 events 的那一处（第一次出现）
idx = text.find(old_state)
assert idx > 0, "state update block not found"
text = text[:idx] + new_state + text[idx + len(old_state):]

p.write_text(text, encoding="utf-8")
print("theodds.py rotation done")
